"""Home page: Coco's room, quick switches and today's stats."""

from __future__ import annotations

import time
from datetime import datetime

from nicegui import run, ui

from coco.screen_time import describe_minutes
from coco_hub import state
from coco_hub.theme import frame, need_login
from coco_hub.view import DOING, TILES, describe, sprite_for, tile_state


@ui.page("/")
def home() -> None:
    if need_login():
        return
    with frame("/", "Home"):
        with ui.element("div").classes("room-wrap"):
            ui.element("div").classes("ear left")
            ui.element("div").classes("ear right")
            with ui.element("div").classes("room") as room:
                ui.element("div").classes("glow")
                ui.element("div").classes("stars")
                ui.element("div").classes("moon")
                ui.html("<i>♥</i><i>♥</i><i>♥</i>").classes("hearts")
                ui.element("div").classes("floor")
                ui.element("div").classes("shadow")
                sprite = ui.element("div").classes("sprite").props('role="img"')
                cord = ui.element("button").classes("cord")
                with cord:
                    ui.element("span").classes("string")
                    with ui.element("span").classes("bead"):
                        ui.icon("pets")
                hint = ui.label().classes("cord-hint")
                busy = ui.label().classes("busy")
                busy.set_visibility(False)

        with ui.column().classes("gap-1 w-full"):
            line = ui.html().classes("status-line")
            sub = ui.label().classes("feels")
        with ui.element("div").classes("driving") as driving:
            ui.icon("smart_toy")
            ui.label("Coco is working inside an app for you").classes("grow")
            ui.button(
                "Stop", icon="pan_tool", on_click=lambda: send("stop_driving", "Stopped")
            ).props("unelevated no-caps color=negative")
        driving.set_visibility(False)

        with ui.element("div").classes("tiles") as tiles_box:
            tiles = {}
            for key, icon, name, kind in TILES:
                with ui.element("button").classes(f"tile {kind}") as t:
                    ui.icon(icon)
                    with ui.column().classes("gap-0"):
                        ui.label(name).classes("t-name")
                        words = ui.label().classes("t-state")
                t.on("click", lambda _e, k=key: flip(k))
                tiles[key] = (t, words)

        kill = ui.element("button").classes("kill")
        with kill:
            ui.icon("visibility_off")
            ui.label("Hide her and close the mic")
        kill.on("click", lambda: send("kill", "Hidden, mic closed"))

        with ui.element("div").classes("card"):
            ui.label("How she feels").classes("eyebrow")
            with ui.element("div").classes("meters q-mt-sm"):
                meters = {}
                for stat, label, icon in (("happiness", "Happy", "favorite"),
                                          ("energy", "Energy", "bolt"),
                                          ("affection", "Cuddly", "pets"),
                                          ("bond", "Bond with you", "favorite")):  # fmt: skip
                    with ui.element("div").classes("meter"):
                        ui.label(label).classes("lbl")
                        with ui.element("div").classes("paws").props(f'aria-label="{label}"'):
                            meters[stat] = [ui.icon(icon) for _ in range(5)]

        with ui.element("div").classes("card"):
            ui.label("Your things").classes("eyebrow")
            with ui.element("div").classes("chips q-mt-sm"):
                for icon, label, to in (("favorite", "Memories", "/memories"),
                                        ("photo_camera", "Screenshots", "/screenshots"),
                                        ("pets", "Activity", "/activity"),
                                        ("face", "About you", "/about")):  # fmt: skip
                    ui.button(label, icon=icon, on_click=lambda _e=None, t=to: ui.navigate.to(t)
                              ).props("unelevated no-caps").classes("chip")  # fmt: skip

        with ui.element("div").classes("card"):
            with ui.row().classes("items-baseline justify-between w-full"):
                ui.label("Today").classes("eyebrow")
            with ui.element("div").classes("facts q-mt-sm"):
                facts = {}
                for key, label in (
                    ("screen", "screen time today"),
                    ("chats", "chats"),
                    ("tasks", "things done"),
                    ("next", "next reminder"),
                ):
                    with ui.element("div").classes("fact"):
                        facts[key] = ui.label("–").classes("big")
                        ui.label(label).classes("lbl")

    shown = {"anim": None, "on": None}

    def paint() -> None:
        snap = state.snapshot
        s = snap.get("status")
        on = s is not None
        if on != shown["on"]:
            room.classes(add="on") if on else room.classes(remove="on")
            cord.props(f'aria-label="{"Turn Coco off" if on else "Turn Coco on"}"')
            hint.set_text("Pull to turn off" if on else "Pull to wake her")
            tiles_box.classes(remove="asleep") if on else tiles_box.classes(add="asleep")
            kill.set_visibility(on)
            shown["on"] = on
        anim = sprite_for(s, state.ANIMS)
        if anim != shown["anim"]:
            frames, fps = state.ANIMS.get(anim, (1, 1.0))
            sprite.style(replace=f"background-image: url('/sprites/{anim}.png'); --n: {frames};"
                                 f" --dur: {frames / max(fps, 0.1):.2f}s")  # fmt: skip
            sprite.props(f'aria-label="Coco, {DOING.get(anim, anim)}"')
            shown["anim"] = anim
        busy_text = {"starting": "Waking her up…", "stopping": "Tucking her in…"}.get(
            snap.get("busy", ""), ""
        )
        busy.set_text(busy_text)
        busy.set_visibility(bool(busy_text))
        driving.set_visibility(bool((s or {}).get("driving")))
        big, small = describe(s)
        line.set_content(big)
        sub.set_text(small)
        for key, (t, words) in tiles.items():
            if not on:
                t.classes(remove="on")
                t.props("disabled")
                words.set_text("—")
                continue
            value, text = tile_state(key, s)
            t.props(remove="disabled") if value is not None else t.props("disabled")
            t.classes(add="on") if value else t.classes(remove="on")
            t.props(f'aria-pressed="{str(bool(value)).lower()}"')
            words.set_text(text)
        mood = (s or {}).get("mood") or {}
        for stat, icons in meters.items():
            full = round(max(0.0, min(1.0, float(mood.get(stat, 0)))) * 5)
            for i, icon in enumerate(icons):
                icon.classes(remove="off") if i < full else icon.classes(add="off")
        facts["chats"].set_text(str(today_chats()))
        facts["tasks"].set_text(str(today_tasks()))
        facts["next"].set_text(next_reminder())
        today = (s or {}).get("screen_today")
        facts["screen"].set_text("–" if today is None else describe_minutes(today * 60))

    async def toggle_power() -> None:
        if state.snapshot.get("busy"):
            return
        cord.classes(add="pulled")
        ui.timer(0.6, lambda: cord.classes(remove="pulled"), once=True)
        on = state.snapshot.get("status") is not None
        state.snapshot["busy"] = "stopping" if on else "starting"
        paint()
        try:
            ok = await run.io_bound(state.coco.stop if on else state.coco.start)
        finally:
            state.snapshot["busy"] = ""
        await state.refresh()
        if not ok:
            ui.notify("Coco didn't respond. Check the log in %LOCALAPPDATA%\\Coco\\logs.",
                      type="negative")  # fmt: skip
        paint()

    async def flip(key: str) -> None:
        s = state.snapshot.get("status")
        if s is None:
            ui.notify("Turn Coco on first: pull the lamp cord.")
            return
        value, _ = tile_state(key, s)
        if value is None:
            return
        # "Wandering" shows the opposite of her "paused" setting
        await send("set", None, key=key, value=value if key == "paused" else not value)

    async def send(name: str, done: str | None, **kw) -> None:
        try:
            r = await run.io_bound(state.coco.command, name, **kw)
        except RuntimeError as exc:
            ui.notify(str(exc), type="negative")
            return
        if r is None:
            ui.notify("Coco isn't answering right now.", type="warning")
        else:
            if r.get("status"):
                state.snapshot.update(status=r["status"], running=True)
            if done:
                ui.notify(done)
        paint()

    cord.on("click", toggle_power)
    paint()
    ui.timer(1.0, paint)


def today_chats() -> int:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return state.store.count_chats(since=start)


def today_tasks() -> int:
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return sum(1 for a in state.store.activity(300) if a.kind == "tool" and a.time >= start)


def next_reminder() -> str:
    pending = [r for r in state.store.reminders() if r.due >= time.time() - 60]
    if not pending:
        return "none"
    due = datetime.fromtimestamp(min(r.due for r in pending))
    same_day = due.date() == datetime.now().date()
    return due.strftime("%H:%M") if same_day else due.strftime("%a %H:%M")

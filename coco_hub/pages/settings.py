"""Settings page."""

from __future__ import annotations

from datetime import datetime

from nicegui import app, run, ui

from coco.agent.guard import DEFAULT_EXCLUDED
from coco.core.config import UserSettings
from coco.core.launch import AUTOSTART_NAME, autostart_command
from coco.core.secrets import Secrets
from coco.desktop import windows as win
from coco.voice.music_words import MusicWords
from coco_hub import state
from coco_hub.auth import valid_pin
from coco_hub.theme import frame, need_login, title

COCO_AUTOSTART = AUTOSTART_NAME
SIZES = {"S": "Small", "M": "Medium", "L": "Large", "XL": "Extra large"}


async def apply(key: str, value) -> bool:
    """One setting, live through Coco if she's on, else straight into settings.json."""
    if state.snapshot.get("status") is not None:
        try:
            if key == "size":
                r = await run.io_bound(state.coco.command, "set_size", size=value)
            else:
                r = await run.io_bound(state.coco.command, "set", key=key, value=value)
        except RuntimeError as exc:
            ui.notify(str(exc), type="negative")
            return False
        if r and r.get("status"):
            state.snapshot.update(status=r["status"])
            state.snapshot["last_status"] = r["status"]
        return r is not None
    s = UserSettings.load()
    if key == "keep_running":  # (the hub can do this one itself)
        from coco.core import keeper_task

        s.keep_running = value
        s.save()
        await run.io_bound(keeper_task.install if value else keeper_task.uninstall)
        return True
    if key.startswith("tool:"):
        tool, off = key.removeprefix("tool:"), set(s.disabled_tools)
        off.discard(tool) if value else off.add(tool)
        s.disabled_tools = sorted(off)
    elif key == "autostart":
        set_coco_autostart(value)
        return True
    elif key in (
        "size", "voice", "history", "phone_speaks", "start_dashboard", "start_tailscale",
        "memory", "web_lookups",
    ):  # fmt: skip
        setattr(s, key, value)
    else:
        ui.notify("Turn Coco on to change that.", type="warning")
        return False
    s.save()
    return True


def set_coco_autostart(on: bool) -> None:
    win.set_autostart(COCO_AUTOSTART, autostart_command() if on else None)


def current() -> dict:
    """Settings as Coco reports them, or from settings.json when she's off."""
    s = state.snapshot.get("status") or state.snapshot.get("last_status")
    saved = UserSettings.load()
    return {
        "size": (s or {}).get("size") or saved.size or "M",
        "sizes": (s or {}).get("sizes") or list(SIZES),
        "voice": (s or {}).get("voice", saved.voice if saved.voice is not None else True),
        "history": (s or {}).get("history", saved.history),
        "phone_speaks": (s or {}).get("phone_speaks", saved.phone_speaks),
        "autostart": win.autostart_enabled(COCO_AUTOSTART),
        "tools": (s or {}).get("tools"),
        "disabled": set(saved.disabled_tools),
        "break_minutes": (s or {}).get("break_minutes", saved.break_minutes),
        "web_lookups": (s or {}).get("web_lookups", saved.web_lookups),
        "monitor_mode": (s or {}).get("monitor_mode", saved.monitor or "primary"),
        "monitors": (s or {}).get("monitors", []),
        "keep_chats_days": (s or {}).get("keep_chats_days", saved.keep_chats_days),
    }


def section(title: str, sub: str = ""):
    card = ui.element("div").classes("card")
    with card:
        ui.label(title).classes("eyebrow")
        if sub:
            ui.label(sub).classes("quiet text-sm")
    return card


def switch_row(label: str, sub: str, value: bool, key: str):
    with ui.element("div").classes("item"):
        with ui.column().classes("grow gap-0"):
            ui.label(label)
            if sub:
                ui.label(sub).classes("sub")
        sw = ui.switch(value=bool(value)).props(f'aria-label="{label}" color=primary')

    async def changed(e) -> None:
        if not await apply(key, bool(e.value)):
            sw.set_value(not e.value)  # (didn't stick: show the real state)

    sw.on_value_change(changed)
    return sw


@ui.page("/settings")
def settings_page() -> None:
    if need_login():
        return
    if state.snapshot.get("status"):
        state.snapshot["last_status"] = state.snapshot["status"]
    c = current()
    with frame("/settings", "Settings"):
        title("Settings", "tune")

        with section("Coco"):
            with ui.element("div").classes("item"):
                with ui.column().classes("grow gap-0"):
                    ui.label("Size on screen")
                size = ui.toggle({k: k for k in c["sizes"]}, value=c["size"]).props(
                    "unelevated rounded toggle-color=primary toggle-text-color=dark "
                    "color=grey-2 text-color=grey-8"
                )

                async def size_changed(e) -> None:
                    await apply("size", e.value)

                size.on_value_change(size_changed)
            screens = {"primary": "Main screen", "follow": "Follow me"}
            for i, m in enumerate(c["monitors"], 1):
                main = ", main" if m.get("primary") else ""
                screens[m["device"]] = f"Screen {i} ({m['size']}{main})"
            if c["monitor_mode"] not in screens:
                screens[c["monitor_mode"]] = c["monitor_mode"]
            with ui.element("div").classes("item"):
                with ui.column().classes("grow gap-0"):
                    ui.label("Which screen")
                    n = len(c["monitors"])
                    hint = (
                        "Follow me: she moves to the screen you're working on."
                        if n != 1
                        else "You have one screen now; this matters with two."
                    )
                    ui.label(hint).classes("sub")
                where = (
                    ui.select(screens, value=c["monitor_mode"])
                    .props('outlined dense aria-label="Which screen"')
                    .classes("w-40")
                )

                async def screen_changed(e) -> None:
                    if state.snapshot.get("status") is not None:
                        try:
                            await run.io_bound(state.coco.command, "set_monitor", value=e.value)
                        except RuntimeError as exc:
                            ui.notify(str(exc), type="negative")
                            return
                    else:
                        saved_now = UserSettings.load()
                        saved_now.monitor = e.value
                        saved_now.save()
                    ui.notify(f"She'll live on: {screens.get(e.value, e.value)}")

                where.on_value_change(screen_changed)
            switch_row("Voice", "She says her answers out loud.", c["voice"], "voice")
            with ui.element("div").classes("item"):
                with ui.column().classes("grow gap-0"):
                    ui.label("Break reminders")
                    ui.label("After this long at the screen without a break.").classes("sub")
                breaks = ui.select(
                    {0: "Off", 30: "30 min", 45: "45 min", 60: "1 hour", 90: "1.5 hours",
                     120: "2 hours"},
                    value=c["break_minutes"],
                ).props('outlined dense aria-label="Break reminders"').classes("w-32")  # fmt: skip

                async def breaks_changed(e) -> None:
                    minutes = int(e.value)
                    if state.snapshot.get("status") is not None:
                        await run.io_bound(state.coco.command, "set_break_minutes", minutes=minutes)
                    else:
                        s = UserSettings.load()
                        s.break_minutes = minutes
                        s.save()
                    ui.notify("Break reminders off" if not minutes
                              else f"She'll suggest a break after {e.value} minutes")  # fmt: skip

                breaks.on_value_change(breaks_changed)
            switch_row("Speak replies to this page",
                       "Off: when you chat from here, she answers silently on the laptop.",
                       c["phone_speaks"], "phone_speaks")  # fmt: skip
            switch_row("Start Coco with Windows", "", c["autostart"], "autostart")

        with section("Her skills", "What she's allowed to do on this PC."):
            if not c["tools"]:
                ui.label("Turn Coco on once to see her skills here.").classes("sub q-mt-sm")
            for t in c["tools"] or []:
                switch_row(t["label"], "", t["on"], f"tool:{t['name']}")

        words = MusicWords()
        with section(
            "Music she should know",
            "Songs and artists you ask for, one per line. She hears these names right even "
            "when they sound unclear.",
        ):
            music = (
                ui.textarea(value="\n".join(words.mine), placeholder="Seedhe Maut\nMadira")
                .props('outlined autogrow aria-label="Music she should know"')
                .classes("w-full q-mt-sm")
            )
            with ui.element("div").classes("item"):
                ui.label(
                    f"She also learned {len(words.learned)} names from songs she played."
                ).classes("grow sub")

                def save_music() -> None:
                    names = [n.strip() for n in (music.value or "").splitlines() if n.strip()]
                    words.set_mine(names)
                    ui.notify(f"Saved {len(names)} names. Coco uses them right away.")

                ui.button("Save", icon="music_note", on_click=save_music).props(
                    "unelevated no-caps color=primary text-color=dark"
                )

        with section(
            "Apps Coco must not touch",
            "When she works inside apps for you (do it for me), she refuses these, one per line. "
            "She never types into terminals or password boxes anyway.",
        ):
            saved = UserSettings.load().excluded_apps
            apps_box = (
                ui.textarea(value="\n".join(saved if saved is not None else DEFAULT_EXCLUDED))
                .props('outlined autogrow aria-label="Apps Coco must not touch"')
                .classes("w-full q-mt-sm")
            )

            async def save_apps() -> None:
                apps = [a.strip() for a in (apps_box.value or "").splitlines() if a.strip()]
                if state.snapshot.get("status") is not None:
                    await run.io_bound(state.coco.command, "set_excluded_apps", apps=apps)
                else:
                    s = UserSettings.load()
                    s.excluded_apps = apps
                    s.save()
                ui.notify(f"Saved: she won't touch {len(apps)} apps.")

            with ui.element("div").classes("item"):
                ui.label("Bank, payment and password apps are on it already.").classes("grow sub")
                ui.button("Save", icon="block", on_click=save_apps).props(
                    "unelevated no-caps color=primary text-color=dark"
                )

        with (
            section("Memory", "Facts about you she keeps and uses (only on this laptop)."),
            ui.element("div").classes("item"),
        ):
            ui.label("See, add or delete what she remembers").classes("grow")
            ui.button(
                "Memories", icon="favorite", on_click=lambda: ui.navigate.to("/memories")
            ).props("flat no-caps")

        with section(
            "Web lookups",
            "She looks things up on Wikipedia (and Brave Search, with a key) and answers in "
            "her own words. Off: nothing you ask leaves the laptop.",
        ):
            switch_row("Let her look things up", "Only your question is sent, nothing else.",
                       c["web_lookups"], "web_lookups")  # fmt: skip
            have_key = bool(Secrets().get("brave.api_key"))
            with ui.element("div").classes("item"):
                with ui.column().classes("grow gap-0"):
                    ui.label("Brave Search key (optional)")
                    ui.label("Saved" if have_key else "For news and recent things: a free key "
                             "from brave.com/search/api").classes("sub")  # fmt: skip
                key_box = (
                    ui.input(placeholder="Paste key", password=True)
                    .props('outlined dense aria-label="Brave Search key"')
                    .classes("w-40")
                )

                def save_key() -> None:
                    key = (key_box.value or "").strip()
                    Secrets().set("brave.api_key", key or None)
                    key_box.set_value("")
                    ui.notify("Brave key saved" if key else "Brave key removed")

                ui.button("Save", on_click=save_key).props("flat no-caps")

        with section("Chat history", "Kept only on this laptop, in coco.db."):
            switch_row("Keep chat history", "Off: new chats aren't saved.", c["history"],
                       "history")  # fmt: skip
            with ui.element("div").classes("item"):
                with ui.column().classes("grow gap-0"):
                    ui.label("Keep messages for")
                    oldest = state.store.oldest_chat_time()
                    since = (
                        f", oldest from {datetime.fromtimestamp(oldest):%d %b %Y}" if oldest else ""
                    )
                    ui.label(f"{state.store.count_chats()} saved{since}. Older ones are deleted "
                             "by themselves.").classes("sub")  # fmt: skip
                keep = (
                    ui.select(
                        {0: "Forever", 7: "1 week", 30: "1 month", 90: "3 months", 365: "1 year"},
                        value=c["keep_chats_days"],
                    )
                    .props('outlined dense aria-label="Keep messages for"')
                    .classes("w-32")
                )

                async def keep_changed(e) -> None:
                    days = int(e.value)
                    if state.snapshot.get("status") is not None:
                        await run.io_bound(state.coco.command, "set_keep_chats", days=days)
                    else:
                        s = UserSettings.load()
                        s.keep_chats_days = days
                        s.save()
                        state.store.prune_chats(days)
                    ui.notify("Messages are kept forever" if not days
                              else f"Messages older than {days} days are deleted")  # fmt: skip

                keep.on_value_change(keep_changed)
            with ui.element("div").classes("item"):
                ui.label(f"{state.store.count_chats()} messages saved").classes("grow sub")
                ui.button("Delete all", icon="delete_forever", on_click=lambda: confirm_wipe()
                          ).props("flat no-caps color=negative")  # fmt: skip

        with section("Security"):
            with ui.element("div").classes("item"):
                ui.label("Change PIN").classes("grow")
                ui.button("Change", on_click=lambda: pin_dialog()).props("flat no-caps")
            with ui.element("div").classes("item"):
                with ui.column().classes("grow gap-0"):
                    ui.label("Sign out everywhere")
                    ui.label("Every phone and browser will need the PIN again.").classes("sub")
                ui.button("Sign out", on_click=lambda: sign_out_all()).props(
                    "flat no-caps color=negative"
                )

        with section("When the laptop starts", "Coco starts with Windows and brings these up."):
            saved = UserSettings.load()
            switch_row("Keep Coco and her dashboard running",
                       "Brings them back after sleep, a shutdown that didn't finish, or a freeze "
                       "(checks when you unlock, when the laptop wakes, and every 5 minutes). "
                       "Quitting her yourself keeps her off.",
                       saved.keep_running, "keep_running")  # fmt: skip
            switch_row("Start this dashboard",
                       "So your phone can reach her right after the laptop starts.",
                       saved.start_dashboard, "start_dashboard")  # fmt: skip
            switch_row("Connect Tailscale",
                       "Starts Tailscale's tray app: that's what lets your phone connect.",
                       saved.start_tailscale, "start_tailscale")  # fmt: skip
            with ui.element("div").classes("item"):
                ui.label("Ollama (her brain) always starts with her when it's needed.").classes(
                    "grow sub"
                )
            with ui.element("div").classes("item"):
                ui.label("Sign out on this device").classes("grow")
                ui.button("Sign out", on_click=lambda: sign_out_here()).props("flat no-caps")

    def confirm_wipe() -> None:
        with ui.dialog() as d, ui.card().classes("card"):
            ui.label("Delete all chat history?").classes("display text-xl")
            ui.label("Every saved message goes, on this laptop. This can't be undone.").classes(
                "quiet"
            )
            with ui.row().classes("justify-end w-full q-mt-md"):
                ui.button("Keep it", on_click=d.close).props("flat no-caps")

                def wipe() -> None:
                    n = state.store.delete_all_chats()
                    d.close()
                    ui.notify(f"Deleted {n} messages")
                    ui.navigate.reload()

                ui.button("Delete all", on_click=wipe).props("unelevated no-caps color=negative")
        d.open()

    def pin_dialog() -> None:
        with ui.dialog() as d, ui.card().classes("card").style("min-width: 300px"):
            ui.label("Change PIN").classes("display text-xl")
            old = ui.input("Current PIN", password=True).props("outlined dense inputmode=numeric")
            new = ui.input("New PIN", password=True).props("outlined dense inputmode=numeric")
            again = ui.input("New PIN again", password=True).props(
                "outlined dense inputmode=numeric"
            )
            err = ui.label().classes("text-negative text-sm")

            def save() -> None:
                result = state.auth.check(old.value or "")
                if result != "ok":
                    err.set_text("Too many tries: wait a bit." if result == "locked"
                                 else "The current PIN is wrong.")  # fmt: skip
                    return
                problem = valid_pin(new.value or "")
                if problem:
                    err.set_text(problem)
                    return
                if new.value != again.value:
                    err.set_text("The new PINs don't match.")
                    return
                state.auth.set_pin(new.value)
                app.storage.user.update(auth_v=state.auth.version)  # this device stays in
                d.close()
                ui.notify("PIN changed. Other devices will need the new PIN.")

            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=d.close).props("flat no-caps")
                ui.button("Save", on_click=save).props("unelevated no-caps color=primary "
                                                       "text-color=dark")  # fmt: skip
        d.open()

    def sign_out_all() -> None:
        state.auth.sign_out_everywhere()
        ui.navigate.to("/login")

    def sign_out_here() -> None:
        app.storage.user.clear()
        ui.navigate.to("/login")

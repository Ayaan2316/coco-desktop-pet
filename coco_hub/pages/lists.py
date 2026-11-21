"""Notes, reminders and activity."""

from __future__ import annotations

import contextlib
import time
from datetime import datetime, timedelta

from nicegui import run, ui

from coco_hub import state
from coco_hub.theme import frame, need_login, title

TOOL_ICON = {
    "spotify": "music_note", "youtube": "smart_display", "screenshot": "photo_camera",
    "explain_screen": "visibility", "open_app": "apps", "open_file": "description",
    "web_search": "travel_explore", "reminder": "alarm", "notes": "sticky_note_2",
    "focus": "timer", "system_status": "memory", "media_control": "graphic_eq",
    "whatsapp": "chat", "started": "power_settings_new", "stopped": "power_off",
}  # fmt: skip


def when_text(t: float) -> str:
    d = datetime.fromtimestamp(t)
    today = datetime.now().date()
    if d.date() == today:
        return d.strftime("Today %H:%M")
    if d.date() == today + timedelta(days=1):
        return d.strftime("Tomorrow %H:%M")
    if d.date() == today - timedelta(days=1):
        return d.strftime("Yesterday %H:%M")
    return d.strftime("%a %d %b, %H:%M")


async def tell_coco_reminders() -> None:
    """Coco keeps one timer for the next reminder: tell her the list changed."""
    if state.snapshot.get("status") is not None:
        with contextlib.suppress(RuntimeError):
            await run.io_bound(state.coco.command, "reload_reminders")


# --- notes
@ui.page("/notes")
def notes_page() -> None:
    if need_login():
        return
    with frame("/notes", "Notes"):
        title("Notes", "edit_note")
        with ui.element("div").classes("card"):
            with ui.row().classes("w-full no-wrap items-center gap-2"):
                new = (
                    ui.input(placeholder="Write a note")
                    .props('outlined dense maxlength=500 aria-label="New note"')
                    .classes("flex-grow")
                )
                add = ui.button("Add", icon="add").props("unelevated color=primary text-color=dark")
            show_done = ui.switch("Show done", value=False).classes("q-mt-xs quiet")
            box = ui.column().classes("w-full gap-0 q-mt-sm")

    def render() -> None:
        box.clear()
        notes = state.store.notes(include_done=show_done.value)
        with box:
            if not notes:
                with ui.element("div").classes("empty w-full"):
                    ui.element("div").classes("nap")
                    ui.label("No notes").classes("display")
                    ui.label("Add one here, or tell Coco “note that …”.")
            for n in reversed(notes):
                with ui.element("div").classes("item w-full"):
                    ui.checkbox(value=n.done, on_change=lambda e, i=n.id: done(i, e.value)).props(
                        f'aria-label="Done: {n.text[:40]}"'
                    )
                    with ui.column().classes("grow gap-0"):
                        ui.label(n.text).classes("done-text" if n.done else "")
                        ui.label(when_text(n.created)).classes("sub mono")
                    ui.button(icon="delete_outline", on_click=lambda i=n.id: delete(i)).props(
                        'flat round color=grey-5 aria-label="Delete note"'
                    )

    def save() -> None:
        text = (new.value or "").strip()
        if text:
            state.store.add_note(text)
            new.set_value("")
            render()

    def done(note_id: int, value: bool) -> None:
        state.store.set_note_done(note_id, value)
        render()

    def delete(note_id: int) -> None:
        state.store.delete_note(note_id)
        ui.notify("Note deleted")
        render()

    add.on_click(save)
    new.on("keydown.enter", save)
    show_done.on_value_change(lambda _e: render())
    render()


# --- reminders
@ui.page("/reminders")
def reminders_page() -> None:
    if need_login():
        return
    soon = datetime.now() + timedelta(hours=1)
    with frame("/reminders", "Reminders"):
        title("Reminders", "alarm")
        with ui.element("div").classes("card"):
            text = (
                ui.input(placeholder="Remind me to…")
                .props('outlined dense maxlength=200 aria-label="Reminder text"')
                .classes("w-full")
            )
            with ui.row().classes("w-full no-wrap items-center gap-2 q-mt-sm"):
                day = (
                    ui.input("Day", value=soon.strftime("%Y-%m-%d"))
                    .props("outlined dense type=date")
                    .classes("flex-grow")
                )
                at = (
                    ui.input("Time", value=soon.strftime("%H:%M"))
                    .props("outlined dense type=time")
                    .classes("w-32")
                )
            add = (
                ui.button("Add reminder", icon="add_alarm")
                .props("unelevated color=primary text-color=dark")
                .classes("w-full q-mt-sm")
            )
        box = ui.element("div").classes("card")

    def render() -> None:
        box.clear()
        items = state.store.reminders()
        with box:
            ui.label("Coming up").classes("eyebrow")
            if not items:
                with ui.element("div").classes("empty"):
                    ui.element("div").classes("nap")
                    ui.label("Nothing coming up").classes("display")
                    ui.label("Add one above, or ask Coco “remind me at 6 to call home”.")
            for r in items:
                overdue = r.due < time.time()
                with ui.element("div").classes("item"):
                    ui.icon("timer" if r.kind == "timer" else "alarm").classes(
                        "text-xl " + ("text-negative" if overdue else "quiet")
                    )
                    with ui.column().classes("grow gap-0"):
                        ui.label(r.text or "(timer)")
                        ui.label(("Overdue · " if overdue else "") + when_text(r.due)).classes(
                            "sub mono"
                        )
                    ui.button(icon="delete_outline", on_click=lambda i=r.id: delete(i)).props(
                        'flat round color=grey-5 aria-label="Delete reminder"'
                    )

    async def save() -> None:
        words = (text.value or "").strip()
        if not words:
            ui.notify("Write what to remind you about.")
            return
        try:
            due = datetime.strptime(f"{day.value} {at.value}", "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            ui.notify("Pick a day and a time.", type="warning")
            return
        if due < time.time():
            ui.notify("That time has already passed.", type="warning")
            return
        state.store.add_reminder(words, due)
        text.set_value("")
        ui.notify(f"Reminder set for {when_text(due)}")
        render()
        await tell_coco_reminders()

    async def delete(reminder_id: int) -> None:
        state.store.delete_reminder(reminder_id)
        ui.notify("Reminder deleted")
        render()
        await tell_coco_reminders()

    add.on_click(save)
    render()


# --- activity
@ui.page("/activity")
def activity_page() -> None:
    if need_login():
        return
    with frame("/", "Activity"):
        title("Activity", "pets")
        ui.label("What Coco did on this PC, newest first. Kept for 90 days.").classes("quiet")
        box = ui.element("div").classes("card")

    def render() -> None:
        box.clear()
        items = state.store.activity(200)
        with box:
            if not items:
                with ui.element("div").classes("empty"):
                    ui.label("Nothing yet").classes("display")
                    ui.label("Ask her to play a song or take a screenshot.")
            for a in items:
                detail = a.detail.split(" | ", 1)
                with ui.element("div").classes("item"):
                    ui.icon(TOOL_ICON.get(a.name, "bolt")).classes(
                        "text-xl " + ("text-primary" if a.ok else "text-negative")
                    )
                    with ui.column().classes("grow gap-0"):
                        title = a.name.replace("_", " ").capitalize()
                        ui.label(title + ("" if a.ok else " (didn't work)"))
                        said = detail[-1].strip() if detail else ""
                        if said:
                            ui.label(said).classes("sub")
                    ui.label(when_text(a.time)).classes("sub mono text-right")

    render()
    ui.timer(5.0, render)

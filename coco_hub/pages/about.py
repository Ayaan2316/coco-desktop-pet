"""About you: name, notes for Coco, and what's stored where."""

from __future__ import annotations

from datetime import datetime

from nicegui import run, ui

from coco.core.config import UserSettings, load_config
from coco.core.paths import user_data_dir
from coco.voice.music_words import MusicWords
from coco_hub import state
from coco_hub.pages.memories import book
from coco_hub.pages.screenshots import folder, list_shots
from coco_hub.theme import frame, need_login, title

KEEP = {0: "forever", 7: "1 week", 30: "1 month", 90: "3 months", 365: "1 year"}


def stored_rows() -> list[tuple[str, str, str, str]]:
    """(icon, what, details, link) for everything she keeps about you."""
    st = state.store
    saved = UserSettings.load()
    s = state.snapshot.get("status") or {}
    chats = st.count_chats()
    oldest = st.oldest_chat_time()
    keep = KEEP.get(s.get("keep_chats_days", saved.keep_chats_days), "forever")
    since = f", since {datetime.fromtimestamp(oldest):%d %b %Y}" if oldest else ""
    shots = list_shots()
    mb = sum(p.stat().st_size for p in shots) / 1e6
    words = MusicWords()
    rows = [
        ("favorite", "Memories", f"{book().count()} facts about you", "/memories"),
        ("forum", "Chat history", f"{chats} messages{since} · kept {keep}", "/chat"),
        ("sticky_note_2", "Notes", f"{len(st.notes(include_done=True))} notes", "/notes"),
        ("alarm", "Reminders", f"{len(st.reminders())} coming up", "/reminders"),
        ("photo_camera", "Screenshots", f"{len(shots)} pictures · {mb:.1f} MB", "/screenshots"),
        ("pets", "Activity", "what she did on the PC · kept 90 days", "/activity"),
        (
            "music_note",
            "Music names",
            f"{len(words.mine)} yours + {len(words.learned)} learned from songs she played",
            "/settings",
        ),  # fmt: skip
    ]
    if s:
        rows.append(("schedule", "Screen time", f"{s.get('screen_today', 0)} min today "
                     "(only today's total, nothing per app)", ""))  # fmt: skip
    return rows


@ui.page("/about")
def about_page() -> None:
    if need_login():
        return
    saved = UserSettings.load()
    name_now = saved.owner_name or load_config().owner_name
    with frame("/", "About you"):
        title("About you", "face")
        with ui.element("div").classes("card"):
            ui.label("You").classes("eyebrow")
            name = (
                ui.input("Your name", value=name_now)
                .props('outlined dense maxlength=40 aria-label="Your name"')
                .classes("w-full q-mt-sm")
            )
            about = (
                ui.textarea(
                    "About you",
                    value=saved.about_me,
                    placeholder="e.g. I'm an ECE student, I love Seedhe Maut and cold coffee, "
                    "I code in Python and C.",
                )
                .props('outlined autogrow maxlength=600 aria-label="About you"')
                .classes("w-full q-mt-sm")
            )
            ui.label("She keeps this in mind in every chat, never reads it back to you.").classes(
                "sub q-mt-xs"
            )
            save_btn = (
                ui.button("Save", icon="check")
                .props("unelevated no-caps color=primary text-color=dark")
                .classes("q-mt-sm")
            )
        with ui.element("div").classes("card"):
            ui.label("What Coco keeps about you").classes("eyebrow")
            for icon, what, details, link in stored_rows():
                with ui.element("div").classes("item"):
                    ui.icon(icon).classes("text-primary text-xl")
                    with ui.column().classes("grow gap-0"):
                        ui.label(what)
                        ui.label(details).classes("sub")
                    if link:
                        ui.button(
                            icon="chevron_right",
                            on_click=lambda _e=None, to=link: ui.navigate.to(to),
                        ).props(  # fmt: skip
                            f'flat round color=grey-6 aria-label="Open {what}"'
                        )
        with ui.element("div").classes("card"):
            ui.label("Where it lives").classes("eyebrow")
            for line in (
                f"Everything above: {user_data_dir()}",
                f"Screenshots: {folder()}",
                "Nothing is uploaded. Your phone sees it only through your own Tailscale "
                "link, behind your PIN.",
                "Web lookups (if you turn them on) send only the question you ask.",
            ):
                ui.label(line).classes("sub q-mt-xs path")

    async def save() -> None:
        owner = (name.value or "").strip()[:40]
        text = (about.value or "").strip()[:600]
        if state.snapshot.get("status") is not None:
            try:
                await run.io_bound(state.coco.command, "set_about", owner_name=owner, about_me=text)
            except RuntimeError as exc:
                ui.notify(str(exc), type="negative")
                return
        else:
            s = UserSettings.load()
            s.owner_name, s.about_me = owner, text
            s.save()
        ui.notify("Saved. She'll keep it in mind~")

    save_btn.on_click(save)

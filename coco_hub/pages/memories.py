"""Memories page."""

from __future__ import annotations

from nicegui import run, ui

from coco.core.config import UserSettings, load_config
from coco.memory import MemoryBook, ollama_embedder
from coco_hub import state
from coco_hub.pages.lists import when_text
from coco_hub.pages.settings import apply
from coco_hub.theme import frame, need_login, title

_book: MemoryBook | None = None


def book() -> MemoryBook:
    global _book
    if _book is None:
        _book = MemoryBook(state.store, ollama_embedder(load_config().brain.host))
    return _book


@ui.page("/memories")
def memories_page() -> None:
    if need_login():
        return
    s = state.snapshot.get("status") or {}
    on = s.get("memory", UserSettings.load().memory)
    with frame("/", "Memories"):
        title("Memories", "favorite")
        ui.label("Things Coco remembers about you. She uses them when they fit what you ask, "
                 "and they never leave this laptop.").classes("quiet")  # fmt: skip
        with ui.element("div").classes("card"):
            with ui.element("div").classes("item"):
                with ui.column().classes("grow gap-0"):
                    ui.label("Remember things about me")
                    ui.label("Off: she neither saves nor uses memories.").classes("sub")
                sw = ui.switch(value=bool(on)).props('color=primary aria-label="Remember things"')

                async def switched(e) -> None:
                    if not await apply("memory", bool(e.value)):
                        sw.set_value(not e.value)

                sw.on_value_change(switched)
            with ui.row().classes("w-full no-wrap items-center gap-2 q-mt-sm"):
                new = (
                    ui.input(placeholder="Tell her something to remember")
                    .props('outlined dense maxlength=300 aria-label="New memory"')
                    .classes("flex-grow")
                )
                add = ui.button("Add", icon="add").props("unelevated color=primary text-color=dark")
        box = ui.element("div").classes("card")

    def render() -> None:
        box.clear()
        items = book().all()
        with box:
            with ui.row().classes("items-baseline justify-between w-full"):
                ui.label(f"{len(items)} memories").classes("eyebrow")
                if items:
                    ui.button("Forget everything", on_click=lambda: confirm_wipe()).props(
                        "flat no-caps color=negative dense"
                    )
            if not items:
                with ui.element("div").classes("empty"):
                    ui.element("div").classes("nap")
                    ui.label("Nothing yet").classes("display")
                    ui.label("Tell her about yourself, or add something above.")
            for m in items:
                with ui.element("div").classes("item"):
                    ui.icon("favorite" if m.source == "you" else "pets").classes("text-primary")
                    with ui.column().classes("grow gap-0"):
                        ui.label(m.text)
                        how = "you told her to" if m.source == "you" else "she noticed"
                        ui.label(f"{how} · {when_text(m.created)}").classes("sub")
                    ui.button(icon="delete_outline", on_click=lambda i=m.id: forget(i)).props(
                        'flat round color=grey-5 aria-label="Forget this"'
                    )

    async def save() -> None:
        text = (new.value or "").strip()
        if len(text) < 3:
            return
        owner = load_config().owner_name or "They"
        fact = text if text.lower().startswith(owner.lower()) else f"{owner} said: {text}"
        await run.io_bound(book().add, fact, "you", True)
        new.set_value("")
        ui.notify("She'll remember that.")
        render()

    def forget(memory_id: int) -> None:
        book().delete(memory_id)
        ui.notify("Forgotten.")
        render()

    def confirm_wipe() -> None:
        with ui.dialog() as d, ui.card().classes("card"):
            ui.label("Forget everything?").classes("display text-xl")
            ui.label("Every memory goes. This can't be undone.").classes("quiet")
            with ui.row().classes("justify-end w-full q-mt-md"):
                ui.button("Keep them", on_click=d.close).props("flat no-caps")

                def wipe() -> None:
                    n = book().delete_all()
                    d.close()
                    ui.notify(f"Forgot {n} memories.")
                    render()

                ui.button("Forget everything", on_click=wipe).props(
                    "unelevated no-caps color=negative"
                )
        d.open()

    add.on_click(save)
    new.on("keydown.enter", save)
    render()

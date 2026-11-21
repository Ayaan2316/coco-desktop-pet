"""Chat history and sending questions from the dashboard."""

from __future__ import annotations

import asyncio
import html
import time
from datetime import datetime

from nicegui import run, ui

from coco.tools.store import Chat
from coco_hub import state
from coco_hub.theme import frame, need_login, title

SOURCE_ICON = {"voice": "mic", "typed": "keyboard", "phone": "smartphone", "coco": "pets"}
SOURCE_WORD = {"voice": "said", "typed": "typed", "phone": "from here", "coco": ""}
PAGE = 40


def day_label(t: float) -> str:
    d = datetime.fromtimestamp(t).date()
    today = datetime.now().date()
    if d == today:
        return "Today"
    if (today - d).days == 1:
        return "Yesterday"
    return d.strftime("%a %d %b")


def bubble_html(c: Chat) -> str:
    when = datetime.fromtimestamp(c.time).strftime("%H:%M")
    out = []
    if c.question:
        icon = SOURCE_ICON.get(c.source, "chat")
        out.append(
            f'<div class="msg me">{html.escape(c.question)}<div class="meta">'
            f'<i class="q-icon material-icons" style="font-size:13px">{icon}</i>'
            f'{SOURCE_WORD.get(c.source, "")} · <span class="mono">{when}</span></div></div>'
        )
    if c.answer:
        out.append('<div class="coco-row"><div class="avatar" aria-hidden="true"></div>'
                   f'<div class="msg coco">{html.escape(c.answer)}</div></div>')  # fmt: skip
    return "".join(out)


@ui.page("/chat")
def chat_page() -> None:
    if need_login():
        return
    view = {"query": "", "oldest": None, "waiting": None, "since": 0.0}
    with frame("/chat", "Chat") as shell:
        shell.classes(add="with-composer")
        with ui.row().classes("items-end justify-between w-full no-wrap"):
            title("Chat", "forum")
            search = (
                ui.input(placeholder="Search")
                .props('outlined dense clearable debounce="400" aria-label="Search chats"')
                .classes("w-40")
            )
        more = (
            ui.button("Show older", on_click=lambda: load(older=True))
            .props("flat no-caps color=grey-5")
            .classes("self-center")
        )
        thread = ui.element("div").classes("thread w-full")
        typing = ui.html('<div class="coco-row"><div class="avatar"></div><div class="msg coco">'
                         '<span class="typing"><span></span><span></span><span></span></span>'
                         "</div></div>")  # fmt: skip
        typing.set_visibility(False)
        empty = ui.element("div").classes("empty")
        with empty:
            ui.element("div").classes("nap")
            ui.label("Nothing here yet").classes("display")
            ui.label("Ask her something below, or talk to her on the laptop.")

    with ui.element("div").classes("composer"), ui.element("div").classes("box"):
        box = (
            ui.input(placeholder="Message Coco")
            .props('borderless dense maxlength=2000 aria-label="Message Coco"')
            .classes("flex-grow")
        )
        send_btn = ui.button(icon="arrow_upward").props(
            'round unelevated color=primary text-color=dark aria-label="Send"'
        )

    def load(older: bool = False) -> None:
        before = view["oldest"] if older else None
        rows = state.store.chats(limit=PAGE, before_id=before, query=view["query"])
        if not older:
            thread.clear()
        if rows:
            view["oldest"] = rows[-1].id
        chunk = list(reversed(rows))
        parts, last_day = [], None
        for c in chunk:
            day = day_label(c.time)
            if day != last_day:
                parts.append(f'<div class="day">{day}</div>')
                last_day = day
            parts.append(bubble_html(c))
        with thread:
            block = ui.html("".join(parts)).classes("thread")
            if older:
                block.move(thread, target_index=0)
        more.set_visibility(len(rows) == PAGE)
        empty.set_visibility(not rows and not older)
        if not older:
            ui.run_javascript("window.scrollTo(0, document.body.scrollHeight)")

    def on_search(e) -> None:
        view["query"] = (e.value or "").strip()
        view["oldest"] = None
        load()

    search.on_value_change(on_search)

    async def send() -> None:
        text = (box.value or "").strip()
        if not text:
            return
        if state.snapshot.get("status") is None:
            ui.notify("Coco is off. Turn her on from Home (pull the lamp cord).", type="warning")
            return
        box.set_value("")
        send_btn.props("loading")
        try:
            chat_id = await run.io_bound(state.coco.chat, text)
        except RuntimeError as exc:
            ui.notify(str(exc), type="negative")
            chat_id = None
        finally:
            send_btn.props(remove="loading")
        if chat_id is None:
            ui.notify("She didn't get that. Is history paused in Settings?", type="warning")
            load()
            return
        view.update(waiting=chat_id, since=time.time())
        load()
        typing.set_visibility(True)  # (it sits right under the thread: never inside it,
        #  or load()'s thread.clear() would delete it)
        await wait_for_answer(chat_id)

    async def wait_for_answer(chat_id: int) -> None:
        # an answer can come in pieces (her reply, then a tool's result): settle 1.5 s
        last, settled = "", 0.0
        while time.time() - view["since"] < 90:
            await asyncio.sleep(0.6)
            c = state.store.chat(chat_id)
            if c and c.answer:
                if c.answer != last:
                    last, settled = c.answer, time.time()
                    load()
                    typing.set_visibility(True)
                elif time.time() - settled > 1.5:
                    break
        typing.set_visibility(False)
        load()

    box.on("keydown.enter", send)
    send_btn.on_click(send)
    load()

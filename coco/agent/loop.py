"""The agent loop: read the window, ask the model for one action, check it, do it."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from coco.agent.guard import Guard
from coco.agent.ui_tree import UiItem, UiView

log = logging.getLogger(__name__)

ACTIONS = ["click", "type", "keys", "scroll", "open_app", "wait", "ask", "done", "give_up"]
SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": ACTIONS},
        "target": {"type": "integer"},
        "text": {"type": "string"},
    },
    "required": ["thought", "action", "target", "text"],
}

SYSTEM = """You are Coco's hands: you operate ONE Windows app for {owner} through its \
accessibility list. Each turn you get the GOAL, what you did so far, and the CURRENT \
SCREEN: things you can act on, each with a number like #12.
Reply with exactly ONE next action as JSON:
- click: target = the item's number (buttons, menu items, tabs, list items, checkboxes).
- type: target = the box's number (0 = the focused one), text = what to type. Typing into \
a box replaces its text; in a document it types at the cursor.
- keys: text = a key combination like "ctrl+s", "enter", "ctrl+t", "f2", "down", "tab".
- scroll: text = "down" or "up".
- open_app: text = an app's name (only when the goal needs another app).
- wait: something is still loading.
- ask: text = one short question, when the goal is unclear (for example which file).
- done: text = one short sentence saying what you did. Use it as soon as the goal is \
complete: check the CURRENT SCREEN.
- give_up: text = why, when it can't be done in this app.
Rules: use only numbers from the CURRENT SCREEN. One small step at a time. "You can read" \
shows results and states (a calculator's display, a page title): check it. As soon as your \
last action reached the goal, answer done: never add extra steps. To write words, use \
type (not click). Prefer the app's own buttons and menus; use well-known shortcuts when \
obvious (ctrl+s save, ctrl+n new, ctrl+t new tab, ctrl+l address bar, f2 rename, \
ctrl+shift+n new folder, ctrl+shift+p VS Code commands). Do only what the goal asks. \
Never type passwords or codes. "thought" is a few words.
Examples:
- GOAL "write hi there" with #1 document "Text editor" -> \
{{"action": "type", "target": 1, "text": "hi there"}}
- GOAL "12 times 34" in Calculator -> {{"action": "type", "target": 0, "text": "12*34="}} \
then, when You can read shows "Display is 408" -> {{"action": "done", "text": "It's 408."}}
- GOAL "open a new tab", DONE SO FAR has "click ... Add New Tab -> invoked" -> \
{{"action": "done", "text": "Opened a new tab."}}"""


@dataclass
class Outcome:
    ok: bool
    kind: str  # done | gave_up | asked | stopped | refused | said_no | limit | error
    summary: str
    steps: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _norm(decision: dict) -> dict:
    action = str(decision.get("action", "")).strip().lower().replace(" ", "_")
    try:
        target = int(decision.get("target") or 0)
    except (TypeError, ValueError):
        target = 0
    return {"action": action if action in ACTIONS else "give_up", "target": target,
            "text": str(decision.get("text") or "").strip(),
            "thought": str(decision.get("thought") or "")[:120]}  # fmt: skip


def describe(decision: dict, item: UiItem | None) -> str:
    a, t = decision["action"], decision["text"]
    if a == "click":
        return f'click #{decision["target"]} "{item.name if item else "?"}"'
    if a == "type":
        where = f' into #{decision["target"]} "{item.name}"' if item else ""
        return f'type "{t[:50]}"{where}'
    if a in ("keys", "scroll", "open_app"):
        return f"{a.replace('_', ' ')} {t}"
    return a


class Agent:
    def __init__(
        self,
        chat_json: Callable[[list[dict], dict], dict],
        read_view: Callable[[int], UiView],
        act: Callable[[dict, UiItem | None, int], str],
        guard: Guard,
        *,
        confirm: Callable[[str], bool],
        open_app: Callable[[str], int | None] = lambda name: None,
        stopped: Callable[[], bool] = lambda: False,
        log_step: Callable[[str, bool], None] = lambda text, ok: None,
        owner: str = "you",
        max_steps: int = 15,
        max_seconds: float = 180.0,
        settle: float = 0.6,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.chat_json, self.read_view, self.act, self.guard = chat_json, read_view, act, guard
        self.confirm, self.open_app, self.stopped = confirm, open_app, stopped
        self.log_step = log_step
        self.owner, self.max_steps, self.max_seconds = owner, max_steps, max_seconds
        self.settle, self.clock, self.sleep = settle, clock, sleep

    def run(self, goal: str, hwnd: int) -> Outcome:
        t0 = self.clock()
        done: list[str] = []
        recent: list[tuple] = []

        def finish(ok: bool, kind: str, summary: str) -> Outcome:
            self.log_step(f"{kind}: {summary}", ok)
            return Outcome(ok, kind, summary, done, self.clock() - t0)

        for step in range(1, self.max_steps + 1):
            if self.stopped():
                return finish(False, "stopped", "You stopped me.")
            if self.clock() - t0 > self.max_seconds:
                return finish(False, "limit", f"That took too long ({self.max_seconds:.0f} s).")
            view = None
            for _ in range(2):
                try:
                    view = self.read_view(hwnd)
                    break
                except Exception as exc:  # noqa: BLE001 - window busy / closed
                    log.info("do_it: reading the window failed: %s", exc)
                    self.sleep(1.0)
            if view is None:
                return finish(False, "error", "I lost sight of that window, so I stopped.")
            hwnd = view.hwnd or hwnd  # (she follows the app's dialogs / new windows)
            refused = self.guard.check_app(view)
            if refused:
                return finish(False, "refused", refused)
            messages = [
                {"role": "system", "content": SYSTEM.format(owner=self.owner)},
                {"role": "user", "content": self._prompt(goal, done, view)},
            ]
            try:
                decision = _norm(self.chat_json(messages, SCHEMA))
            except Exception as exc:  # noqa: BLE001 - model down / bad JSON
                log.warning("do_it: the AI didn't answer: %s", exc)
                return finish(False, "error", "My AI didn't answer, so I stopped.")
            action, text = decision["action"], decision["text"]
            if action == "done":
                return finish(True, "done", text or "Done!")
            if action == "give_up":
                return finish(False, "gave_up", text or "I couldn't do that here.")
            if action == "ask":
                return finish(False, "asked", text or "Which one do you mean?")
            item = view.item(decision["target"]) if decision["target"] else None
            if action == "click" and item is None:
                done.append(f"{step}. click #{decision['target']} -> there's no such item")
                continue
            verdict = self.guard.check(decision, item, view, goal)
            if verdict.kind == "refuse":
                return finish(False, "refused", verdict.why)
            if verdict.kind == "confirm":
                if not self.confirm(verdict.why):
                    return finish(False, "said_no", "Okay, I didn't do it.")
                if self.stopped():
                    return finish(False, "stopped", "You stopped me.")
            # by name, not number: new tabs / rows renumber the list every step
            key = (action, text, item.name if item else f"#{decision['target']}")
            recent = [*recent[-2:], key]
            if len(recent) == 3 and len(set(recent)) == 1:
                return finish(False, "limit", "I was going in circles, so I stopped.")
            what = describe(decision, item)
            if action == "open_app":
                new = self.open_app(text)
                result = f"opened {text}" if new else f"couldn't open {text}"
                hwnd = new or hwnd
            elif action == "wait":
                result = "waited"
                self.sleep(1.5)
            else:
                try:
                    result = self.act(decision, item, hwnd)
                except Exception as exc:  # noqa: BLE001 - element gone, app busy...
                    result = f"failed ({exc})"
            done.append(f"{step}. {what} -> {result}")
            self.log_step(f"{what} -> {result}", not result.startswith("failed"))
            self.sleep(self.settle)
        return finish(False, "limit", f"I stopped after {self.max_steps} steps.")

    @staticmethod
    def _prompt(goal: str, done: list[str], view: UiView) -> str:
        so_far = "\n".join(done[-10:]) if done else "(nothing yet)"
        return f"GOAL: {goal}\n\nDONE SO FAR:\n{so_far}\n\nCURRENT SCREEN:\n{view.text()}"


def live_act(decision: dict, item: UiItem | None, hwnd: int) -> str:
    """The real actions (actions.py) for a guarded decision."""
    from coco.agent import actions

    a, text = decision["action"], decision["text"]
    if a == "click":
        return actions.click(item)
    if a == "type":
        return actions.type_into(item, text, hwnd)
    if a == "keys":
        return actions.press(text, hwnd)
    if a == "scroll":
        return actions.scroll(text or "down", hwnd, item)
    raise ValueError(a)


def ollama_json(backend) -> Callable[[list[dict], dict], dict]:
    """chat_json() on Coco's Ollama backend (streams, then parses the JSON)."""

    def chat(messages: list[dict], schema: dict) -> dict:
        return json.loads("".join(backend.stream_chat(messages, schema)))

    return chat

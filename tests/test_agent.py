"""Do it for me: the tree summary, every safety rail, and the step loop with a scripted
AI (no real clicks, no model). Trees in tests/data/ui_trees were recorded from this PC's
Notepad, Calculator and Settings (personal text removed)."""

import json
from pathlib import Path

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.agent.guard import Guard
from coco.agent.loop import Agent
from coco.agent.ui_tree import UiItem, UiView, build_view
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext

TREES = Path(__file__).parent / "data" / "ui_trees"


def tree(name: str) -> UiView:
    d = json.loads((TREES / f"{name}.json").read_text(encoding="utf-8"))
    return build_view(d["title"], d["app"], d["nodes"])


# --- what she sees
def test_calculator_summary():
    v = tree("calculator")
    text = v.text()
    assert '#18 button "Multiply by"' in text and '"Display is 0"' in text
    assert "" not in text  # icon-font glyphs are dropped
    assert len(text) < 1500 and len(v.items) == 34


def test_notepad_and_settings_summaries():
    n = tree("notepad")
    assert n.focused().kind == "Document" and n.focused().value == "hello"
    assert sum(1 for i in n.items if i.name == "File") == 1  # menu "File" > button "File"
    s = tree("settings")
    lines = [i.line() for i in s.items if i.name == "Show more settings"]
    assert len(lines) == 9 and all("(in " in line for line in lines)  # which device


def test_long_lists_are_cut():
    items = [UiItem(i, "Button", f"Button {i}") for i in range(1, 400)]
    text = UiView("Big", "big.exe", items).text(max_lines=150)
    assert "#150 " in text and "#151 " not in text and "more not shown" in text


# --- the rails
def view_with(*items: UiItem, app: str = "notepad.exe", title: str = "Untitled - Notepad"):
    return UiView(title, app, list(items))


def act(action, target=0, text=""):
    return {"action": action, "target": target, "text": text}


@pytest.fixture
def guard():
    return Guard(["phonepe", "bank"])


def test_never_types_secrets(guard):
    pw = UiItem(1, "Edit", "Password", password=True)
    otp = UiItem(2, "Edit", "Enter OTP")
    box = UiItem(3, "Edit", "Search")
    v = view_with(pw, otp, box)
    assert guard.check(act("type", 1, "hunter2"), pw, v).kind == "refuse"
    assert guard.check(act("type", 2, "482913"), otp, v).kind == "refuse"
    assert guard.check(act("type", 3, "lofi"), box, v).ok


def test_never_types_in_terminals(guard):
    assert guard.check_app(view_with(app="cmd.exe", title="Command Prompt"))
    assert guard.check_app(view_with(app="windowsterminal.exe", title="PowerShell"))
    term = UiItem(1, "Edit", "Terminal 1, powershell", focused=True)
    v = view_with(term, app="code.exe", title="project - Visual Studio Code")
    assert guard.check(act("type", 1, "rm -rf"), term, v).kind == "refuse"
    assert guard.check(act("keys", 0, "enter"), None, v).kind == "refuse"
    new = UiItem(2, "MenuItem", "New Terminal")  # opening the panel is fine
    assert guard.check(act("click", 2), new, view_with(new, app="code.exe")).ok


def test_excluded_apps(guard):
    assert "don't touch" in guard.check_app(view_with(app="phonepe.exe", title="PhonePe"))
    assert guard.check_app(view_with(title="HDFC Bank - NetBanking", app="chrome.exe"))
    assert guard.check(act("open_app", 0, "PhonePe"), None, view_with()).kind == "refuse"
    assert guard.check_app(view_with()) is None


def test_risky_things_ask_first(guard):
    send = UiItem(1, "Button", "Send")
    msg = UiItem(2, "Edit", "Type a message", focused=True)
    v = view_with(send, msg)
    assert guard.check(act("click", 1), send, v).kind == "confirm"
    assert guard.check(act("keys", 0, "enter"), None, v).kind == "confirm"
    assert guard.check(act("keys", 0, "alt+f4"), None, v).kind == "confirm"
    assert guard.check(act("keys", 0, "win+r"), None, v).kind == "refuse"
    toggle = UiItem(1, "Button", "Bluetooth", checked=False)
    s = view_with(toggle, app="systemsettings.exe", title="Settings")
    assert guard.check(act("click", 1), toggle, s).kind == "confirm"
    bold = UiItem(1, "Button", "Bold (Ctrl+B)")
    assert guard.check(act("click", 1), bold, view_with(bold)).ok


# --- the loop
class Script:
    """A pretend AI: returns the next scripted decision each step."""

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.prompts = []

    def __call__(self, messages, schema):
        self.prompts.append(messages[-1]["content"])
        if not self.decisions:
            raise RuntimeError("no more")
        d = self.decisions.pop(0)
        return {"thought": "", "target": 0, "text": "", **d}


def run(script, view=None, confirm=True, stopped=lambda: False, **kw):
    view = view or tree("calculator")
    did, logged = [], []
    agent = Agent(
        script, lambda hwnd: view, lambda d, item, hwnd: did.append((d["action"], item.name
                                                                     if item else None)) or "ok",
        Guard([]), confirm=lambda q: confirm, stopped=stopped,
        log_step=lambda text, ok: logged.append(text), sleep=lambda s: None, **kw,
    )  # fmt: skip
    return agent.run("work out 12 x 34", 123), did, logged


def test_does_the_steps_then_done():
    s = Script(
        {"action": "click", "target": 23},
        {"action": "click", "target": 24},
        {"action": "click", "target": 18},
        {"action": "done", "text": "It's 408."},
    )
    out, did, logged = run(s)
    assert out.ok and out.kind == "done" and out.summary == "It's 408."
    assert did == [("click", "One"), ("click", "Two"), ("click", "Multiply by")]
    assert "Multiply by" in s.prompts[0] and "1. click #23" in s.prompts[-1]
    assert logged[-1] == "done: It's 408."


def test_no_means_no_and_stop_means_stop():
    close = Script({"action": "click", "target": 4})  # "Close Calculator": asks first
    out, did, _ = run(close, confirm=False)
    assert out.kind == "said_no" and did == []
    out, did, _ = run(Script({"action": "click", "target": 23}), stopped=lambda: True)
    assert out.kind == "stopped" and did == []


def test_limits():
    out, did, _ = run(Script(*[{"action": "click", "target": 23}] * 5))
    assert out.kind == "limit" and "circles" in out.summary and len(did) == 2
    many = Script(*[{"action": "click", "target": 22 + (i % 9)} for i in range(20)])
    out, did, _ = run(many, max_steps=6)
    assert out.kind == "limit" and len(did) == 6
    out, _, _ = run(Script())  # the AI doesn't answer
    assert out.kind == "error"
    gives_up = Script({"action": "click", "target": 99}, {"action": "give_up", "text": "no"})
    out, did, _ = run(gives_up)
    assert out.kind == "gave_up" and did == []


def test_refuses_password_boxes_in_the_loop():
    pw = UiItem(1, "Edit", "Password", password=True)
    out, did, _ = run(Script({"action": "type", "target": 1, "text": "x"}), view=view_with(pw))
    assert out.kind == "refused" and did == []


# --- the tool
TOOLS = ["do_it", "open_app", "web_search", "spotify", "youtube", "notes"]


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("in notepad, write hello world", {"target": "notepad", "text": "write hello world"}),
        ("in vs code open a new terminal", {"target": "vs code", "text": "open a new terminal"}),
        ("do it for me: rename this file to report_final",
         {"text": "rename this file to report final"}),
        ("chrome mein naya tab khol do", {"target": "chrome", "text": "naya tab"}),
    ],
)  # fmt: skip
def test_phrases(said, args):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == "do_it", said
    assert call.args == args


@pytest.mark.parametrize("said", ["open notepad", "play kesariya", "note that milk is over",
                                  "in my opinion cats are great"])  # fmt: skip
def test_not_do_it(said):
    call = parse_task(said, TOOLS)
    assert call is None or call.tool != "do_it", said


def test_tool_reports_the_outcome():
    from coco.agent.loop import Outcome

    class Host:
        def run(self, goal, app):
            self.got = (goal, app)
            return Outcome(True, "done", "Typed it.", ["1. type"], 2.0)

    host = Host()
    ctx = ToolContext(services={"agent_host": host})
    r = REGISTRY.run(ToolCall("do_it", {"target": "notepad", "text": "write hi"}), ctx, ["do_it"])
    assert r.ok and r.say.startswith("Done! Typed it.") and host.got == ("write hi", "notepad")


def test_repeats_are_caught_even_when_numbers_change():
    """New tabs renumber the list: three "Add New Tab" clicks in a row still stop her."""
    views = iter(
        UiView("Untitled - Notepad", "notepad.exe",
               [UiItem(i + 1, "TabItem", "Untitled") for i in range(k)]
               + [UiItem(k + 1, "Button", "Add New Tab", patterns=("invoke",))])
        for k in range(1, 10)
    )  # fmt: skip
    s = Script(*[{"action": "click", "target": k + 1} for k in range(1, 10)])
    did = []
    agent = Agent(s, lambda hwnd: next(views), lambda d, item, hwnd: did.append(item.name) or "ok",
                  Guard([]), confirm=lambda q: True, sleep=lambda x: None)  # fmt: skip
    out = agent.run("open a new tab", 1)
    assert out.kind == "limit" and did == ["Add New Tab", "Add New Tab"]


def test_settings_toggles_ask_first_in_the_real_settings_window(guard):
    bt = UiItem(1, "Button", "Bluetooth", patterns=("toggle",), checked=False)
    v = view_with(bt, app="applicationframehost.exe", title="Settings")
    assert guard.check(act("click", 1), bt, v).kind == "confirm"

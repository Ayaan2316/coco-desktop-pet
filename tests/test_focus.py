"""Focus mode: phrases, work/break rounds, the tool."""

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.tools.focus import FocusSession, clock_text
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext

TOOLS = ["focus", "reminder", "notes", "open_app", "web_search"]


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("start focus", {"target": "start"}),
        ("Coco, start a pomodoro", {"target": "start"}),
        ("focus mode", {"target": "start"}),
        ("focus for 50 minutes", {"target": "start", "amount": "50"}),
        ("let's focus for 45 minutes", {"target": "start", "amount": "45"}),
        ("focus mode on karo", {"target": "start"}),
        ("padhai mode on karo", {"target": "start"}),
        ("stop focus", {"target": "stop"}),
        ("end the focus session", {"target": "stop"}),
        ("focus mode band karo", {"target": "stop"}),
        ("how much focus time is left?", {"target": "status"}),
    ],
)
def test_phrases(said, args):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == "focus", said
    assert call.args == args


def test_start_focus_is_not_open_app():
    assert parse_task("start focus", TOOLS).tool == "focus"


def test_rounds():
    t = [1000.0]
    s = FocusSession(25, 5, clock=lambda: t[0])
    s.start()
    assert s.working and s.remaining() == 25 * 60
    t[0] += 25 * 60
    p = s.advance()
    assert p.kind == "break" and s.remaining() == 5 * 60
    t[0] += 5 * 60
    p = s.advance()
    assert p.kind == "work" and p.round == 2
    s.start(50)
    assert s.remaining() == 50 * 60 and s.phase.round == 1
    s.stop()
    assert not s.active and s.remaining() == 0
    assert clock_text(18 * 60 + 42) == "18:42"


def test_tool_replies():
    t = [0.0]
    ctx = ToolContext(services={"focus": FocusSession(25, 5, clock=lambda: t[0])})
    r = REGISTRY.run(ToolCall("focus", {"target": "start", "amount": "50"}), ctx, TOOLS)
    assert r.say.startswith("Focus mode on: 50 minutes") and r.data == {"focus": "start"}
    t[0] += 60
    r = REGISTRY.run(ToolCall("focus", {"target": "status"}), ctx, TOOLS)
    assert r.say.startswith("49:00 left in this work round")
    r = REGISTRY.run(ToolCall("focus", {"target": "stop"}, lang="hinglish"), ctx, TOOLS)
    assert "Focus mode band" in r.say and r.speech_hi
    r = REGISTRY.run(ToolCall("focus", {"target": "start"}, said="focus for 2 hours"), ctx, TOOLS)
    assert "120 minutes" in r.say

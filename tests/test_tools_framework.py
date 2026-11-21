"""Tool framework: allow-list, confirm, runner, intent helpers, the brain's tool field."""

import json

import pytest

from coco.brain.brain import Brain
from coco.brain.prompt import PromptContext, build_system_prompt, reply_schema
from coco.brain.reply_parser import ReplyParser
from coco.brain.schemas import BrainReply
from coco.core.bus import EventBus
from coco.core.catdef import load_cat
from coco.core.config import BrainConfig
from coco.core.events import Topic
from coco.tools.intents import clean, is_no, is_yes
from coco.tools.registry import Registry, ToolCall, ToolResult
from coco.tools.runner import ToolContext, ToolRunner


def make_registry():
    reg = Registry()
    ran = []

    @reg.tool("wave", "wave a paw", words=r"wave|haath")
    def wave(call, ctx):
        ran.append(call)
        return ToolResult(say="*waves*")

    @reg.tool("forget", "delete a note", risk="confirm", words=r"delete|forget")
    def forget(call, ctx):
        ran.append(call)
        return ToolResult(say="Deleted.")

    return reg, ran


def runner_for(reg, allowed=("wave", "forget"), disabled=(), answer=None):
    bus = EventBus()
    results = []
    bus.subscribe(Topic.TOOL_RESULT, lambda e: results.append(e.payload))
    asked = []

    def ask(call, question, done):
        asked.append(question)
        if answer is not None:
            done(answer)

    r = ToolRunner(
        bus,
        ToolContext(),
        lambda: list(allowed),
        disabled=lambda: list(disabled),
        ask_confirm=ask,
        registry=reg,
        threaded=False,
    )
    return r, bus, results, asked


def test_allow_list_is_enforced_in_code():
    reg, ran = make_registry()
    r, bus, results, _ = runner_for(reg, allowed=("forget",))
    r.handle(ToolCall("wave"))
    r.handle(ToolCall("rm_rf"))  # not even registered
    bus.drain()
    assert ran == [] and all(not res.ok for _c, res in results)
    with pytest.raises(PermissionError):
        reg.run(ToolCall("wave"), None, allowed=["forget"])


def test_tray_switch_turns_a_tool_off():
    reg, ran = make_registry()
    r, bus, results, _ = runner_for(reg, disabled=("wave",))
    assert r.available() == ["forget"]
    r.handle(ToolCall("wave"))
    bus.drain()
    assert ran == [] and not results[0][1].ok


def test_risky_tools_ask_first():
    reg, ran = make_registry()
    r, bus, results, asked = runner_for(reg, answer=False)
    r.handle(ToolCall("forget", {"text": "buy ESP32"}))
    bus.drain()
    assert asked == ["Are you sure? (buy ESP32)"] and ran == []  # said no: nothing deleted
    r, bus, results, asked = runner_for(reg, answer=True)
    r.handle(ToolCall("forget", {"text": "buy ESP32"}, lang="hinglish"))
    bus.drain()
    assert asked == ["Pakka? (buy ESP32)"] and len(ran) == 1 and results[-1][1].say == "Deleted."


def test_risky_tool_without_anyone_to_ask_never_runs():
    reg, ran = make_registry()
    bus = EventBus()
    r = ToolRunner(bus, ToolContext(), lambda: ["forget"], registry=reg, threaded=False)
    r.handle(ToolCall("forget"))
    assert ran == []


def test_a_crashing_tool_is_reported_not_raised():
    reg = Registry()

    @reg.tool("boom", "explode")
    def boom(call, ctx):
        raise RuntimeError("kaboom")

    r, bus, results, _ = runner_for(reg, allowed=("boom",))
    r.handle(ToolCall("boom"))
    bus.drain()
    assert results[0][1].ok is False and "Oops" in results[0][1].say


def test_yes_no_in_english_and_hinglish():
    for y in ("Yes", "yeah sure", "haan", "Haan ji, kar do", "ok", "हाँ"):
        assert is_yes(y), y
    for n in ("No", "nope", "nahi", "mat karo", "cancel", "rehne do"):
        assert is_no(n), n
    assert not is_yes("what is a capacitor")
    assert clean("Hey Coco, could you please take a screenshot!") == "take a screenshot"


def test_schema_has_tool_fields_only_when_tools_exist():
    assert "tool" not in reply_schema()["properties"]
    s = reply_schema(tools=["screenshot"])
    assert list(s["properties"])[:4] == ["emotion", "action", "tool", "args"]
    assert s["properties"]["tool"]["enum"] == ["none", "screenshot"]
    assert set(s["properties"]["args"]["required"]) == {"target", "text", "when", "amount"}


def test_prompt_lists_only_allowed_tools():
    cat = load_cat("coco")
    plain = build_system_prompt(cat, PromptContext(owner="Sam"))
    assert "cannot see the screen" in plain and "PC tasks" not in plain
    with_tools = build_system_prompt(cat, PromptContext(owner="Sam"), "  wave: wave a paw")
    assert "PC tasks you CAN do" in with_tools and "wave: wave a paw" in with_tools
    assert "cannot see the screen" not in with_tools


def test_parser_reads_tool_and_args():
    raw = json.dumps(
        {
            "emotion": "happy",
            "action": "none",
            "tool": "screenshot",
            "args": {"target": "window", "text": "", "when": "", "amount": ""},
            "text": "On it!",
        }
    )
    p = ReplyParser()
    for i in range(0, len(raw), 7):
        p.feed(raw[i : i + 7])
    assert p.tool == "screenshot"
    assert p.finish() == ("On it!", "happy")
    assert p.tool_args == {"target": "window"}


class ScriptedBackend:
    """Streams one fixed JSON reply."""

    def __init__(self, reply: dict):
        self.payload = json.dumps(reply)
        self.calls = []

    def stream_chat(self, messages, schema):
        self.calls.append((messages, schema))
        for i in range(0, len(self.payload), 9):
            yield self.payload[i : i + 9]

    def prewarm(self):
        pass


def ask(reply: dict, question: str, tools=("screenshot",)):
    import coco.tools.registry as reg_mod

    reg_mod.REGISTRY.specs.setdefault(
        "screenshot",
        reg_mod.ToolSpec(
            "screenshot",
            "take a screenshot",
            lambda c, x: ToolResult(),
            words=__import__("re").compile("screenshot|screen", 2),
        ),
    )
    bus = EventBus()
    got = []
    bus.subscribe(Topic.BRAIN_REPLY, lambda e: got.append(e.payload))
    backend = ScriptedBackend(reply)
    brain = Brain(BrainConfig(), load_cat("coco"), bus, backend, ensure=lambda: True)
    brain.tools = lambda: list(tools)
    brain.ask(question)
    brain.threads[-1].join(5)
    bus.drain()
    final = [r for r in got if isinstance(r, BrainReply) and r.final][-1]
    return final, backend


SHOT = {
    "emotion": "happy",
    "action": "none",
    "tool": "screenshot",
    "args": {"target": "screen", "text": "", "when": "", "amount": ""},
    "text": "On it!",
}


def test_brain_passes_on_a_tool_the_words_support():
    final, backend = ask(SHOT, "can you grab my screen real quick")
    assert final.tool == "screenshot" and final.tool_args == {"target": "screen"}
    assert "tool" in backend.calls[0][1]["properties"]


def test_brain_drops_a_tool_the_words_dont_support():
    final, _ = ask(SHOT, "what's the weather like?")
    assert final.tool == "none"
    final, _ = ask(SHOT, "hey coco")  # a greeting never does anything
    assert final.tool == "none"


def test_follow_ups_have_no_tools_and_carry_facts_and_images():
    bus = EventBus()
    backend = ScriptedBackend({"emotion": "curious", "action": "none", "text": "54 %!"})
    brain = Brain(BrainConfig(), load_cat("coco"), bus, backend, ensure=lambda: True)
    brain.tools = lambda: ["screenshot"]
    brain.ask("how's my battery?", facts="Battery 54 %, charging", image_b64="AAAA", tools=False)
    brain.threads[-1].join(5)
    messages, schema = backend.calls[0]
    assert "tool" not in schema["properties"]
    assert "Battery 54 %, charging" in messages[-1]["content"]
    assert messages[-1]["images"] == ["AAAA"]


def test_questions_and_statements_never_trigger_actions():
    """The model sometimes picks a tool for these; the brain's guards drop them."""
    from coco.brain.brain import asks_about, tells_not_asks

    assert asks_about("how do reminders work on android?")
    assert tells_not_asks("I took a screenshot earlier for my report")
    assert tells_not_asks("I forgot my notes at college")
    for request in ("I need to concentrate for a bit", "I want to watch lofi on youtube",
                    "can you take a screenshot?", "nudge me in an hour",
                    "my laptop feels slow, can you check it?"):  # fmt: skip
        assert not asks_about(request) and not tells_not_asks(request), request

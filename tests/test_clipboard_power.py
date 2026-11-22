"""Clipboard and power, with a fake clipboard and fake power switches."""

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.tools.clipboard import clean_reply, looks_secret
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext

TOOLS = ["clipboard", "power", "open_app", "web_search", "media_control", "spotify"]


class FakeClip:
    def __init__(self, text=""):
        self.text = text

    def read(self):
        return self.text

    def write(self, text):
        self.text = text


class FakePower:
    def __init__(self):
        self.did = []

    def __getattr__(self, name):
        return lambda: self.did.append(name)


def ctx(clip="", answer=""):
    return ToolContext(services={"clipboard": FakeClip(clip), "power": FakePower(),
                                 "last_answer": lambda: answer})  # fmt: skip


# --- clipboard
def test_reads_what_you_copied_for_her_ai():
    c = ctx("TypeError: 'NoneType' object is not subscriptable")
    r = REGISTRY.run(ToolCall("clipboard", {"target": "read"}), c, TOOLS)
    assert r.ok and "NoneType" in r.facts and r.say == ""  # her AI explains it


def test_secrets_and_empty_clipboards_are_not_read():
    for secret in ("482913", "Hunter2!x9", "sk-AbC123xyz_Q"):
        assert looks_secret(secret), secret
    for fine in ("hello world", "https://example.com/A1b2!", "Kesariya", "ESP32"):
        assert not looks_secret(fine), fine
    r = REGISTRY.run(ToolCall("clipboard", {"target": "read"}), ctx("Hunter2!x9"), TOOLS)
    assert not r.ok and "password" in r.say and not r.facts
    r = REGISTRY.run(ToolCall("clipboard", {"target": "read"}), ctx(""), TOOLS)
    assert not r.ok and "empty" in r.say


def test_long_text_is_cut():
    r = REGISTRY.run(ToolCall("clipboard", {"target": "read"}), ctx("word " * 2000), TOOLS)
    assert "first 4000 of 10000 characters" in r.facts


def test_copies_her_last_answer_without_cat_noises():
    c = ctx(answer="*purrs* Ohm's law: V = I x R. Meow~")
    r = REGISTRY.run(ToolCall("clipboard", {"target": "copy"}), c, TOOLS)
    assert r.ok and c.services["clipboard"].text == "Ohm's law: V = I x R."
    assert clean_reply("*slow blink* Done! *tail swish*") == "Done!"


# --- power
def test_power_runs_and_risky_ones_ask_first():
    spec = REGISTRY.specs["power"]
    for target in ("sleep", "restart", "shutdown"):
        assert spec.needs_confirm(ToolCall("power", {"target": target}))
    for target in ("lock", "screen_off", "cancel"):
        assert not spec.needs_confirm(ToolCall("power", {"target": target}))
    c = ctx()
    r = REGISTRY.run(ToolCall("power", {"target": "shutdown"}), c, TOOLS)
    assert r.ok and c.services["power"].did == ["shutdown"] and "60 s" in r.say


@pytest.mark.parametrize(
    ("said", "tool", "args"),
    [
        ("what does this copied error mean?", "clipboard", {"target": "read"}),
        ("explain what i just copied", "clipboard", {"target": "read"}),
        ("translate my clipboard to hindi", "clipboard", {"target": "read"}),
        ("copy your answer", "clipboard", {"target": "copy"}),
        ("ye copy karo", "clipboard", {"target": "copy"}),
        ("lock my laptop", "power", {"target": "lock"}),
        ("laptop lock karo", "power", {"target": "lock"}),
        ("turn off the screen", "power", {"target": "screen_off"}),
        ("put the laptop to sleep", "power", {"target": "sleep"}),
        ("restart my pc", "power", {"target": "restart"}),
        ("shut down the laptop", "power", {"target": "shutdown"}),
        ("cancel the shutdown", "power", {"target": "cancel"}),
        ("shutdown cancel karo", "power", {"target": "cancel"}),
    ],
)
def test_phrases(said, tool, args):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == tool, said
    assert call.args == args


@pytest.mark.parametrize(
    "said",
    ["i want to sleep", "go to sleep", "how do i lock a file in git", "copy paste is so boring",
     "the screen is too bright"],
)  # fmt: skip
def test_not_power_or_clipboard(said):
    call = parse_task(said, TOOLS)
    assert call is None or call.tool not in ("power", "clipboard"), said

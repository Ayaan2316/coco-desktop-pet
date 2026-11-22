"""Memory: saving, merging, recall, forgetting, the tool, and the brain's field."""

import hashlib

import numpy as np
import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.brain.prompt import PromptContext, build_system_prompt, reply_schema
from coco.brain.reply_parser import ReplyParser
from coco.core.catdef import load_cat
from coco.memory import DIM, MemoryBook
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext
from coco.tools.store import Store


def fake_embed(texts):
    """Bag of words -> a vector: texts sharing words point the same way (like meaning)."""
    out = []
    for t in texts:
        v = np.zeros(DIM, np.float32)
        for w in t.lower().replace("'s", "").split():
            w = w.strip(".,!?")
            if len(w) > 2:
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1
        out.append(list(v))
    return out


@pytest.fixture
def book(tmp_path):
    now = [1_000_000.0]
    b = MemoryBook(Store(tmp_path / "coco.db"), fake_embed, per_day=3, clock=lambda: now[0])
    b.now = now
    return b


def test_saves_recalls_and_merges(book):
    assert book.add("Sam's ECE exam is on Friday")
    assert book.add("Sam loves Seedhe Maut songs")
    assert [m.text for m in book.recall("when is the ECE exam?")] == ["Sam's ECE exam is on Friday"]
    book.add("Sam's ECE exam is on Friday!!")  # (real model: Friday ~ "Monday now" 0.96)
    assert book.count() == 2  # merged, not saved twice


def test_filters_trivia_and_caps_a_day(book):
    for junk in ("", "none", "Sam asked what time it is", "ok"):
        assert book.add(junk) is None
    book.add("Sam studies at VIT")
    book.add("Sam has a cat named Coco")
    book.add("Sam likes cold coffee")
    assert book.add("Sam plays football on Sundays") is None  # 3 a day
    assert book.add("Sam plays football on Sundays", source="you", force=True)  # you asked
    book.now[0] += 2 * 86400
    assert book.add("Sam's sister is called Sara")


def test_forget(book):
    m = book.add("Sam's bike is a red Pulsar")
    found = book.find("my bike")
    assert found and found[0].id == m.id
    book.delete(m.id)
    assert book.count() == 0 and book.find("bike") == []


def test_words_only_without_the_model(tmp_path):
    b = MemoryBook(Store(tmp_path / "coco.db"), None)
    b.add("Sam's ECE exam is on Friday")
    assert b.recall("ece exam date")[0].text.startswith("Sam's ECE")


# --- the tool
TOOLS = ["memory", "reminder", "notes", "web_search", "open_app"]


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("remember that my exam is on friday", {"target": "add", "text": "my exam is on friday"}),
        ("remember my bike is red", {"target": "add", "text": "my bike is red"}),
        ("what do you remember about me?", {"target": "list"}),
        ("forget that my exam is on friday", {"target": "forget", "text": "my exam is on friday"}),
        ("meri exam ki date bhool jao", {"target": "forget", "text": "meri exam ki date"}),
    ],
)
def test_phrases(said, args):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == "memory", said
    assert call.args == args


def test_remember_to_stays_a_reminder():
    call = parse_task("remember to call mom at 6", TOOLS)
    assert call is None or call.tool != "memory"


def test_tool(book):
    asked = []
    ctx = ToolContext(services={
        "memory": book,
        "ask": lambda c, q, ch, cb: asked.append((q, ch, cb)),
        "report": lambda c, r: asked.append(r),
    })  # fmt: skip
    r = REGISTRY.run(ToolCall("memory", {"target": "add", "text": "my bike is red"}), ctx, TOOLS)
    assert r.ok and book.count() == 1
    r = REGISTRY.run(ToolCall("memory", {"target": "list"}), ctx, TOOLS)
    assert "bike is red" in r.facts  # her AI says it in her own words
    r = REGISTRY.run(ToolCall("memory", {"target": "forget", "text": "bike"}), ctx, TOOLS)
    assert r is None and asked[0][0] == "Forget which one?"
    _q, choices, picked = asked[0]
    picked(choices[0][0])  # you tap the memory
    assert book.count() == 0 and asked[-1].say.startswith("Forgotten")


# --- the brain
def test_brain_asks_for_and_reads_the_remember_field():
    schema = reply_schema(remember=True)
    assert "remember" in schema["properties"] and "remember" in schema["required"]
    assert "remember" not in reply_schema()["properties"]
    ctx = PromptContext(owner="Sam", memories=["Sam's ECE exam is on Friday"])
    prompt = build_system_prompt(load_cat("coco"), ctx, remember=True)
    assert "Things you remember about Sam" in prompt and "ECE exam is on Friday" in prompt
    assert '"remember"' in prompt
    p = ReplyParser()
    p.feed('{"emotion": "happy", "action": "none", "text": "Good luck!", '
           '"remember": "Sam\'s exam is on Friday"}')  # fmt: skip
    p.finish()
    assert p.remember == "Sam's exam is on Friday"

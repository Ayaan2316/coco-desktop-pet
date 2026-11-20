"""Brain: streaming events, history, cancellation, failures (with the scripted backend)."""

from __future__ import annotations

import json

from coco.brain.backend import BackendError, FakeBackend
from coco.brain.brain import Brain
from coco.brain.schemas import BrainReply, BrainStatus
from coco.core.bus import EventBus
from coco.core.catdef import load_cat
from coco.core.config import BrainConfig
from coco.core.events import Topic


class Harness:
    def __init__(self, backend=None, **cfg):
        self.bus = EventBus()
        self.cfg = BrainConfig(**cfg)
        self.backend = backend or FakeBackend(delay=0)
        self.t = 0.0
        self.brain = Brain(
            self.cfg,
            load_cat("coco"),
            self.bus,
            self.backend,
            ensure=lambda: True,
            clock=lambda: self.t,
        )
        self.replies: list[BrainReply] = []
        self.statuses: list[BrainStatus] = []
        self.bus.subscribe(Topic.BRAIN_REPLY, lambda e: self.replies.append(e.payload))
        self.bus.subscribe(Topic.BRAIN_STATUS, lambda e: self.statuses.append(e.payload))

    def ask(self, q: str) -> int:
        rid = self.brain.ask(q)
        for t in self.brain.threads:
            t.join(timeout=5)
        self.bus.drain()  # deliver on "the main thread"
        return rid

    def final(self) -> BrainReply:
        return [r for r in self.replies if r.final][-1]


def test_reply_streams_then_finishes_with_emotion():
    h = Harness(
        FakeBackend(
            script=lambda q: ("curious", "Resistors limit current."), chunk_size=3, delay=0.001
        )
    )
    rid = h.ask("what does a resistor do?")
    partial = [r for r in h.replies if not r.final]
    assert partial, "should stream partial replies"
    assert all(r.request_id == rid for r in h.replies)
    assert h.final().text == "Resistors limit current."
    assert h.final().emotion == "curious"
    assert [s.state for s in h.statuses] == ["thinking", "ready"]


def test_system_prompt_and_history_are_sent():
    h = Harness(history_turns=2)
    for q in ("one", "two", "three"):
        h.ask(q)
    messages = h.backend.calls[-1]
    assert messages[0]["role"] == "system" and "Coco" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "three"}
    # only the last 2 exchanges (4 messages) are kept as history
    assert [m["content"] for m in messages[1:-1] if m["role"] == "user"] == ["one", "two"]
    assert len(h.brain.history) == 4
    stored = json.loads(h.brain.history[-1]["content"])
    assert set(stored) == {"emotion", "text"}


def test_history_resets_after_a_long_pause():
    h = Harness(history_reset_after=60)
    h.ask("hello")
    assert h.brain.history
    h.t += 61
    h.ask("again")
    assert [m["content"] for m in h.backend.calls[-1] if m["role"] == "user"] == ["again"]


def test_cancel_drops_leftover_chunks():
    h = Harness(FakeBackend(chunk_size=1, delay=0.005))
    h.brain.ask("tell me a long story")
    h.brain.cancel()
    for t in h.brain.threads:
        t.join(timeout=5)
    h.bus.drain()
    assert not any(r.final for r in h.replies)
    assert h.brain.history == []


def test_new_question_replaces_the_old_one():
    h = Harness(FakeBackend(chunk_size=1, delay=0.003))
    first = h.brain.ask("first")
    second = h.brain.ask("second")
    for t in h.brain.threads:
        t.join(timeout=5)
    h.bus.drain()
    finals = [r for r in h.replies if r.final]
    assert [r.request_id for r in finals] == [second]
    assert first != second


def test_offline_posts_status_and_a_sleepy_line():
    h = Harness()
    h.brain._ensure = lambda: False
    h.ask("hi")
    assert h.statuses[-1].state == "offline"
    assert h.final().emotion == "sleepy" and "brain is asleep" in h.final().text


def test_model_missing_tells_you_how_to_fix_it():
    h = Harness(FakeBackend(fail_with=BackendError("model_missing", "not found")))
    h.ask("hi")
    assert h.statuses[-1].state == "model_missing"
    assert f"ollama pull {h.cfg.model}" in h.final().text


def test_runaway_reply_is_cut():
    h = Harness(
        FakeBackend(script=lambda q: ("neutral", "word " * 1000), delay=0), max_reply_chars=100
    )
    h.ask("ramble")
    assert len(h.final().text) <= 101


def test_empty_reply_gets_a_fallback():
    h = Harness(FakeBackend(script=lambda q: ("happy", ""), delay=0))
    h.ask("?")
    assert h.final().text == "Hmm?"

"""The hands-free 'Hey Coco' flow, driven by scripted detector scores (no mic, no models)."""

import numpy as np

from coco.core.bus import EventBus
from coco.core.config import WakeConfig
from coco.core.events import Topic
from coco.voice.wake import (
    CHUNK,
    RATE,
    WakeFlow,
    WakeNet,
    mlp_forward,
    says_her_name,
    strip_wake_word,
)

BLOCK = CHUNK  # feed 80 ms at a time


class FakeDetector:
    """Scores 1.0 for blocks marked 'wake' (a loud 300 Hz tone), else 0."""

    threshold = 0.5

    def __init__(self):
        self.resets = 0

    def feed(self, block):
        return [1.0 if abs(float(block[0]) - 0.5) < 1e-6 else 0.0]

    def reset(self):
        self.resets += 1


def wake_block():
    b = np.full(BLOCK, 0.2, np.float32)
    b[0] = 0.5  # the fake detector's marker
    return b


def speech(seconds):
    t = np.arange(int(seconds * RATE)) / RATE
    return (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def quiet(seconds):
    return np.zeros(int(seconds * RATE), np.float32)


def run_flow(audio_blocks, hear=lambda a: "go to sleep.", confirm_text=None, **kw):
    """hear: audio -> text (called for the part after the wake word, and/or the whole)."""
    bus = EventBus()
    got = []
    for topic in (Topic.LISTEN_START, Topic.LISTEN_END, Topic.HEARD):
        bus.subscribe(topic, lambda e: got.append((e.topic, e.payload)))
    heard_audio = []

    def _hear(audio):
        heard_audio.append(audio)
        return hear(audio)

    flow = WakeFlow(
        WakeConfig(**kw),
        bus,
        FakeDetector(),
        _hear,
        confirm=(lambda a: confirm_text) if confirm_text is not None else None,
    )
    for b in audio_blocks:
        for i in range(0, len(b), BLOCK):
            flow.process(b[i : i + BLOCK])
    bus.drain()
    return flow, got, heard_audio


def chunks(*parts):
    return list(parts)  # each part is split into 80 ms blocks separately


def test_hey_coco_then_a_command():
    flow, got, heard = run_flow(chunks(quiet(1.0), wake_block(), speech(1.0), quiet(1.2)))
    topics = [t for t, _ in got]
    assert topics == [Topic.LISTEN_START, Topic.LISTEN_END, Topic.HEARD]
    assert got[-1][1] == {"ok": True, "reason": "", "text": "go to sleep."}
    # only what came after the wake word (+ cut_back) is transcribed, not "Hey Coco" itself
    assert len(heard) == 1 and len(heard[0]) < (0.15 + 1.0 + 1.0) * RATE + 2 * BLOCK
    assert flow.state == "idle" and flow.stats["woke"] == 1


def test_only_hey_coco_waits_then_asks():
    flow, got, heard = run_flow(
        chunks(quiet(0.5), wake_block(), quiet(5.0)), hear=lambda a: "Hey Coco."
    )
    assert got[-1][1]["reason"] == "wake_only"
    assert len(heard) == 1  # nothing said after it: only the whole clip was checked


def test_command_said_before_the_detector_was_sure():
    """ "Hey Coco, run!" in one go: the hit comes late; the whole clip has it all."""
    flow, got, _ = run_flow(
        chunks(quiet(0.5), wake_block(), quiet(5.0)), hear=lambda a: "Hey Coco, run!"
    )
    assert got[-1][1] == {"ok": True, "reason": "", "text": "run!"}


def test_random_audio_never_becomes_a_command():
    """A false detector hit with nothing after it: Whisper didn't hear her name -> ignore."""
    flow, got, _ = run_flow(
        chunks(quiet(0.5), wake_block(), quiet(5.0)), hear=lambda a: "Play with me."
    )
    assert got[-1][1] == {"ok": False, "reason": "wake_only", "text": ""}  # "Meow?", no command


def test_optional_whisper_double_check_rejects_false_hits():
    flow, got, _ = run_flow(
        chunks(quiet(0.5), wake_block(), speech(1.0), quiet(1.0)), confirm_text="hey cocktail"
    )  # no name: not her
    assert got == [] and flow.stats == {"hits": 1, "rejected": 1, "woke": 0}


def test_paused_while_she_speaks():
    bus = EventBus()
    flow = WakeFlow(WakeConfig(), bus, FakeDetector(), lambda a: "x", confirm=None)
    flow.paused = True
    flow.process(wake_block())
    assert flow.state == "idle" and flow.stats["hits"] == 0


def test_max_seconds_cuts_long_recordings():
    flow, got, heard = run_flow(
        chunks(wake_block(), speech(4.0)),
        max_seconds=2.0,
        hear=lambda a: "tell me a long story",
    )
    assert got[-1][1]["text"] == "tell me a long story"
    assert len(heard[0]) <= (0.15 + 2.0) * RATE + 2 * BLOCK


def test_wake_word_text_helpers():
    assert strip_wake_word("Hey Coco, go to sleep.") == "go to sleep."
    assert strip_wake_word("Hey, Coco.") == ""
    assert strip_wake_word("Coco so jao") == "so jao"
    assert strip_wake_word("what is a capacitor") == "what is a capacitor"
    assert strip_wake_word("Echo, Coco.") == ""
    assert strip_wake_word("Coco, go. Stop.") == "Stop."
    assert strip_wake_word("Hey Coco, go to sleep") == "go to sleep"
    assert strip_wake_word("Hey Coco. So,") == ""
    assert says_her_name("Hey Coco!") and says_her_name("hey koko")
    assert not says_her_name("hey cocktail")


def test_numpy_net_round_trip(tmp_path):
    rng = np.random.default_rng(0)
    layers = [
        (rng.standard_normal((32, 8)).astype(np.float32), np.zeros(8, np.float32)),
        (rng.standard_normal((8, 1)).astype(np.float32), np.zeros(1, np.float32)),
    ]
    net = WakeNet(
        layers, np.zeros(32, np.float32), np.ones(32, np.float32), frames=2, threshold=0.7
    )
    path = tmp_path / "net.npz"
    net.save(path)
    again = WakeNet.load(path)
    x = rng.standard_normal((5, 2, 16)).astype(np.float32)
    assert np.allclose(net.score(x), again.score(x)) and again.threshold == 0.7
    s = mlp_forward(x.reshape(5, -1), layers)
    assert s.shape == (5,) and ((s >= 0) & (s <= 1)).all()

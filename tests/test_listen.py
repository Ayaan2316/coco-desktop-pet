"""Listening: transcript cleanup, silence detection, and how the engine reacts."""

import numpy as np

from coco.core.events import Topic
from coco.voice.stt import RATE, clean_transcript, judge_audio, next_device, to_16k_mono
from tests.conftest import Sim


def test_phantom_whisper_phrases_are_dropped():
    for phantom in ("you", " Thank you. ", "Thanks for watching!", "[BLANK_AUDIO]", "..."):
        assert clean_transcript(phantom) == ""
    assert clean_transcript("  Coco,   go to sleep.  ") == "Coco, go to sleep."


def test_audio_verdicts():
    assert judge_audio(np.zeros(int(0.2 * RATE), np.float32)) == "too_short"
    assert judge_audio(np.zeros(RATE, np.float32)) == "silence"
    t = np.arange(RATE) / RATE
    assert judge_audio((0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)) == "ok"


def test_ear_icon_and_stopping_while_she_listens():
    sim = Sim()
    sim.eng.command("walk")
    sim.step()
    sim.send(Topic.LISTEN_START)
    sim.step()
    assert sim.pose.mood_icon == "ear"  # privacy: always shown while the mic is open
    assert sim.state == "sit"  # she stops to listen
    sim.send(Topic.LISTEN_END, {"cancelled": False})
    sim.step()
    assert sim.state == "think" and sim.pose.mood_icon != "ear"


def test_listening_wakes_her_up():
    sim = Sim(start_in_bed=True)
    sim.run(3, until=lambda s: s.state == "sit")
    sim.eng.force("sleep")
    sim.send(Topic.LISTEN_START)
    sim.step()
    assert sim.state == "wake_up"


def test_cancelled_listening_does_not_think():
    sim = Sim()
    sim.send(Topic.LISTEN_START)
    sim.step()
    sim.send(Topic.LISTEN_END, {"cancelled": True})
    sim.step()
    assert sim.state == "sit" and not sim.eng.listening


def test_next_microphone_round_robin():
    mics = ["Headset Microphone", "Microphone Array"]
    assert next_device(mics, "Headset Microphone") == "Microphone Array"
    assert next_device(mics, "Microphone Array") == "Headset Microphone"
    assert next_device(["Only Mic"], "Only Mic") is None


def test_48k_stereo_becomes_16k_mono():
    t = np.arange(48000) / 48000
    left = np.sin(2 * np.pi * 300 * t)
    stereo = np.stack([left, left], axis=1).astype(np.float32)
    mono = to_16k_mono(stereo, 48000)
    assert mono.dtype == np.float32 and len(mono) == 16000
    assert abs(float(np.sqrt(np.mean(mono**2))) - 0.7071) < 0.01  # same loudness


class _FakeStream:
    def __init__(self):
        self.closed = False

    def stop(self):
        pass

    def close(self):
        self.closed = True


def _ready_listener():
    from coco.core.bus import EventBus
    from coco.core.config import SttConfig
    from coco.voice.stt import Listener

    li = Listener(SttConfig(ring_seconds=1.5, pre_roll_seconds=0.8), EventBus())
    li._stream = _FakeStream()  # pretend the mic is open (no hardware in tests)
    li._rate = RATE
    return li


def _tone(seconds, freq=220.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return (0.1 * np.sin(2 * np.pi * freq * t)).astype(np.float32).reshape(-1, 1)


def test_mic_ready_keeps_only_a_short_rolling_buffer():
    li = _ready_listener()
    for _ in range(100):  # 5 s of audio in 50 ms blocks, while nobody pressed the key
        li._push(_tone(0.05))
    assert li._ring_frames <= 1.55 * RATE  # never more than ~1.5 s in memory
    assert li._chunks == []  # nothing recorded


def test_talk_key_keeps_the_moment_before_the_press():
    li = _ready_listener()
    for _ in range(40):
        li._push(_tone(0.05))
    assert li.start()
    for _ in range(20):  # 1 s of speech after the press
        li._push(_tone(0.05))
    audio = li._stop_recording()
    assert 1.75 * RATE <= len(audio) <= 1.9 * RATE  # 0.8 s pre-roll + 1 s
    assert li.mic_open and not li.recording  # mic stays ready for next time


def test_cancel_and_mic_ready_off_close_the_mic():
    li = _ready_listener()
    li.start()
    stream = li._stream
    li.cancel()
    assert stream.closed and not li.mic_open and not li.recording
    li = _ready_listener()
    li.keep_ready = False
    li.start()
    li._push(_tone(1.0))
    li._stop_recording()
    assert not li.mic_open  # without "mic ready" the mic closes after every sentence

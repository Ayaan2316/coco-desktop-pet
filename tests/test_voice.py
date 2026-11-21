"""Her voice: what gets spoken (text cleaning, sentence streaming) and talk-animation sync."""

from coco.brain.schemas import BrainReply
from coco.core.events import Topic
from coco.voice.speech_text import SentenceStream, speakable, split_sentences
from tests.conftest import Sim


def test_actions_code_and_symbols_are_not_read_out():
    text = (
        "*yawns* Sure! Here you go:\n\n```python\nprint('hi')\n```\n\n"
        "- uses `print`\n- see [docs](https://python.org) 🐾"
    )
    spoken = speakable(text)
    assert "yawns" not in spoken and "print('hi')" not in spoken
    assert "Here's the code." in spoken
    assert "uses print" in spoken and "docs" in spoken
    assert "http" not in spoken and "🐾" not in spoken and "*" not in spoken


def test_sentences_are_split():
    assert split_sentences("Hi! How are you? I'm fine.") == ["Hi!", "How are you?", "I'm fine."]


def test_stream_hands_out_complete_sentences_only():
    s = SentenceStream()
    assert s.feed("Hello there! How a") == ["Hello there!"]
    assert s.feed("Hello there! How are") == []
    assert s.feed("Hello there! How are you? Fi") == ["How are you?"]
    assert s.feed("Hello there! How are you? Fine", final=True) == ["Fine"]
    assert s.feed("Hello there! How are you? Fine", final=True) == []  # nothing twice


def test_stream_stops_reading_very_long_answers():
    s = SentenceStream(max_chars=30)
    out = s.feed("One sentence here. Another sentence here. A third one here.", final=True)
    assert out == ["One sentence here.", "Another sentence here."]


def _chat_until_talk(sim: Sim) -> None:
    sim.send(Topic.CHAT_ASK, "what is a resistor?")
    sim.step()
    sim.send(Topic.BRAIN_REPLY, BrainReply("Mrrp", None, final=False, request_id=0))
    sim.step()
    assert sim.state == "talk"


def test_talk_waits_for_her_voice_to_start_and_finish():
    sim = Sim()
    sim.eng.voice_on = True
    _chat_until_talk(sim)
    sim.send(Topic.BRAIN_REPLY, BrainReply("Mrrp!", "happy", final=True, request_id=0))
    sim.run(1.5)
    assert sim.state == "talk"  # the text is done, but the audio hasn't started yet
    sim.send(Topic.VOICE_START)
    sim.run(3)
    assert sim.state == "talk"  # still speaking
    sim.send(Topic.VOICE_END)
    assert sim.run(1, until=lambda s: s.state == "emote")


def test_talk_gives_up_waiting_if_no_audio_comes():
    sim = Sim()
    sim.eng.voice_on = True  # e.g. no audio device
    _chat_until_talk(sim)
    sim.send(Topic.BRAIN_REPLY, BrainReply("Mrrp!", "happy", final=True, request_id=0))
    assert sim.run(8, until=lambda s: s.state == "emote")


def test_without_voice_talk_follows_the_text_only():
    sim = Sim()
    _chat_until_talk(sim)
    sim.send(Topic.BRAIN_REPLY, BrainReply("Mrrp!", "happy", final=True, request_id=0))
    assert sim.run(0.5, until=lambda s: s.state == "emote")

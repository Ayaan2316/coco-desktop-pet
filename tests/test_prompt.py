"""System prompt: persona from cat.yaml, format rules, time-of-day and mood context."""

from datetime import datetime

from coco.brain.prompt import PromptContext, build_system_prompt, reply_schema, time_phrase
from coco.brain.schemas import EMOTIONS
from coco.core.catdef import load_cat


def test_prompt_has_persona_rules_and_emotions():
    text = build_system_prompt(load_cat("coco"), PromptContext(now=datetime(2026, 1, 1, 15)))
    assert "You are Coco" in text
    for emotion in EMOTIONS:
        assert emotion in text
    assert '"emotion"' in text and '"text"' in text


def test_late_night_teasing_and_mood_context():
    ctx = PromptContext(
        now=datetime(2026, 1, 1, 2, 14),
        mood_words="sleepy, a bit bored",
        doing="napping in your bed",
    )
    text = build_system_prompt(load_cat("coco"), ctx)
    assert "02:14" in text and "still awake" in text
    assert "sleepy, a bit bored" in text
    assert "napping in your bed" in text


def test_time_phrases():
    assert "morning" in time_phrase(datetime(2026, 1, 1, 8))
    assert "afternoon" in time_phrase(datetime(2026, 1, 1, 14))
    assert "late at night" in time_phrase(datetime(2026, 1, 1, 23, 30))


def test_schema_lists_emotion_first_and_requires_all_fields():
    from coco.behaviour.commands import ACTIONS

    schema = reply_schema()
    assert list(schema["properties"]) == ["emotion", "action", "text"]
    hindi = reply_schema(hindi=True)  # Hinglish question: Devanagari copy required
    assert list(hindi["properties"])[-1] == "speech_hi" and "speech_hi" in hindi["required"]
    assert hindi["properties"]["speech_hi"]["minLength"] >= 1
    assert schema["properties"]["emotion"]["enum"] == list(EMOTIONS)
    assert schema["properties"]["action"]["enum"] == ["none", *ACTIONS]
    assert schema["required"] == ["emotion", "action", "text"]


def test_she_knows_your_name_and_never_says_mrrp():
    ctx = PromptContext(now=datetime(2026, 1, 1, 15), owner="Sam")
    text = build_system_prompt(load_cat("coco"), ctx)
    assert "Sam's desktop" in text and "{name}" not in text and "{owner}" not in text
    assert 'Never write "mrrp"' in text

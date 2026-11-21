"""Hinglish: commands in English letters and in Devanagari, and the romaniser."""

import pytest

from coco.behaviour.commands import command_language, parse_command
from coco.core.hindi import has_devanagari, to_roman


@pytest.mark.parametrize(
    ("said", "action"),
    [
        ("Coco, so jao.", "sleep"),
        ("coco so jaao na", "sleep"),
        ("Coco, idhar aao.", "come_here"),
        ("idhar aa ja", "come_here"),
        ("Coco, bhaag!", "run"),
        ("bhaago", "run"),
        ("Coco, ruko.", "stop"),
        ("bas karo yaar", "stop"),
        ("Coco, mere saath khelo.", "play"),
        ("Coco, uth jao.", "get_up"),
        ("Coco, chhup jao.", "peek"),
        ("khidki pe kood jao", "jump_up"),
        ("jump karo", "jump_up"),  # English verb + "karo"
        ("zara stretch karo na", "stretch"),
        # Devanagari, the way Whisper writes Hindi speech
        ("कोको, सो जाओ", "sleep"),
        ("इधर आओ", "come_here"),
        ("कोको, भागो!", "run"),
        ("रुको", "stop"),
        ("मेरे साथ खेलो", "play"),
        ("उठ जाओ", "get_up"),
    ],
)
def test_hinglish_commands(said, action):
    assert parse_command(said) == action
    assert command_language(said) == "hinglish"


@pytest.mark.parametrize(
    "said",
    [
        "Coco, mera code kyun crash ho raha hai?",  # a question, not a command
        "kya tum so rahi ho?",
        "Aaj mera exam bahut bura gaya yaar.",
        "Capacitor kya hota hai, simple mein samjhao.",
        "आज मौसम कैसा है?",
    ],
)
def test_hinglish_conversation_goes_to_the_ai(said):
    assert parse_command(said) is None


def test_english_commands_still_english():
    assert parse_command("Coco, go to sleep") == "sleep"
    assert command_language("Coco, go to sleep") == "en"
    assert command_language("what is a capacitor?") == "en"


def test_romaniser():
    assert to_roman("कोको, सो जाओ") == "koko, so jaao"
    assert to_roman("मेरे साथ खेलो") == "mere saath khelo"
    assert to_roman("आज मौसम कैसा है?") == "aaj mausam kaisa hai?"
    assert to_roman("Coco, run") == "Coco, run"  # English is left alone
    assert has_devanagari("सो") and not has_devanagari("so")


def test_hinglish_detector():
    from coco.core.hindi import looks_hinglish

    for s in (
        "Coco, mera code kyun crash ho raha hai?",
        "Mujhe ek joke sunao.",
        "Thoda music laga do na.",
        "आज मौसम कैसा है?",
        "Coco so jao",
    ):
        assert looks_hinglish(s), s
    for s in (
        "How do I do this?",
        "Do you want to go to sleep?",
        "What is a capacitor?",
        "I want to go to the main market and do a lot",
        "thanks coco!!",
    ):
        assert not looks_hinglish(s), s


def test_parser_reads_the_devanagari_copy():
    from coco.brain.reply_parser import ReplyParser

    raw = (
        '{"emotion": "happy", "action": "none", "text": "Haan, bilkul!", "speech_hi": "हाँ, बिल्कुल!"}'
    )
    p = ReplyParser()
    for i in range(0, len(raw), 5):
        p.feed(raw[i : i + 5])
    assert p.speech_hi == "हाँ, बिल्कुल!"
    assert p.finish() == ("Haan, bilkul!", "happy")


def test_hindi_sentences_are_cut_at_the_danda():
    from coco.voice.speech_text import split_sentences

    assert split_sentences("ठीक है। सो रही हूँ।") == ["ठीक है।", "सो रही हूँ।"]


def test_hinglish_mentions_action():
    from coco.brain.brain import mentions_action

    assert mentions_action("thoda aaram kar lo")
    assert mentions_action("कोको, सो जाओ")
    assert not mentions_action("aaj ka mausam kaisa hai")


def test_hinglish_ear_name_fix():
    from coco.voice.stt import fix_hinglish_name

    assert fix_hinglish_name("co co idhar aao.") == "Coco, idhar aao."
    assert fix_hinglish_name("Ecko ko jump on my window.") == "Coco, jump on my window."
    assert fix_hinglish_name("To to bhaag.") == "Coco, bhaag."
    assert fix_hinglish_name("Aaj mera exam bahut bura gaya.") == "Aaj mera exam bahut bura gaya."


def test_picking_between_the_two_ears():
    """Recorded cases: (english ear, hinglish ear, confidences)."""
    from coco.voice.stt import pick_transcript

    # a Hinglish command from the Hinglish ear beats the English ear's "translation"
    assert pick_transcript(("Coco, go to sleep.", -0.59), ("Ruko.", -0.91)) == "Ruko."
    assert pick_transcript(("Coco, jump. Ciao.", -0.44), ("Coco chup jao.", -0.27)) == (
        "Coco, chup jao."
    )
    # an English command stays English
    assert pick_transcript(("Coco, run.", -0.49), ("Coco run.", -0.82)) == "Coco, run."
    assert pick_transcript(("Come here, Coco.", -0.23), ("Samjhaar dekho.", -0.67)) == (
        "Come here, Coco."
    )
    # otherwise the more confident ear wins
    assert (
        pick_transcript(
            ("Today my exam is very good.", -1.04), ("Aaj mera exam bahut bura gaya.", -0.06)
        )
        == "Aaj mera exam bahut bura gaya."
    )
    assert (
        pick_transcript(("What is a capacitor?", -0.13), ("What is the capacity?", -0.29))
        == "What is a capacitor?"
    )
    assert pick_transcript(("Coco, run.", -0.5), None) == "Coco, run."  # one ear only

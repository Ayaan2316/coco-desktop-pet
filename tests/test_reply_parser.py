"""Streaming reply parser: emotion + text out of partial JSON, escapes, bad output."""

import json

import pytest

from coco.brain.reply_parser import ReplyParser, normalize_emotion, scan_partial


def stream(payload: str, size: int) -> ReplyParser:
    p = ReplyParser()
    for i in range(0, len(payload), size):
        p.feed(payload[i : i + size])
    return p


@pytest.mark.parametrize("size", [1, 2, 3, 7, 1000])
def test_any_chunking_gives_the_same_result(size):
    payload = json.dumps({"emotion": "curious", "text": 'A "cap"\nstores \\ energy ⚡ é 🐾'})
    p = stream(payload, size)
    assert p.emotion == "curious"
    assert p.text == 'A "cap"\nstores \\ energy ⚡ é 🐾'
    assert p.finish() == ('A "cap"\nstores \\ energy ⚡ é 🐾', "curious")


def test_emotion_only_once_complete():
    p = ReplyParser()
    p.feed('{"emotion": "hap')
    assert p.emotion is None  # never act on half a word
    p.feed('py", "text": "Hel')
    assert p.emotion == "happy" and p.text == "Hel"
    p.feed('lo!"}')
    assert p.text == "Hello!"


def test_text_streams_before_it_is_finished():
    p = ReplyParser()
    seen = []
    for chunk in ['{"emotion":"sad",', '"text":"Oh', " no", '..."}']:
        p.feed(chunk)
        seen.append(p.text)
    assert seen == ["", "Oh", "Oh no", "Oh no..."]


def test_escape_split_across_chunks():
    p = ReplyParser()
    p.feed('{"emotion":"love","text":"a\\')
    assert p.text == "a"  # waits for the rest of the escape
    p.feed("nb\\u00")
    assert p.text == "a\nb"
    p.feed('e9"}')
    assert p.text == "a\nbé"


def test_text_before_emotion_still_works():
    p = stream('{"text": "Hi!", "emotion": "love"}', 4)
    assert p.finish() == ("Hi!", "love")


def test_unknown_emotion_becomes_neutral():
    assert stream('{"emotion": "ecstatic", "text": "yay"}', 5).finish() == ("yay", "neutral")
    assert normalize_emotion(" HAPPY ") == "happy"
    assert normalize_emotion(None) == "neutral"


def test_plain_text_reply_falls_back():
    assert stream("Mrrp, hello there!", 3).finish() == ("Mrrp, hello there!", "neutral")


def test_code_fence_wrapper_is_tolerated():
    payload = '```json\n{"emotion": "happy", "text": "ok"}\n```'
    p = stream(payload, 5)
    assert p.text == "ok"
    assert p.finish() == ("ok", "happy")


def test_cut_off_json_uses_what_arrived():
    p = stream('{"emotion": "curious", "text": "A capacitor stores', 6)
    assert p.finish() == ("A capacitor stores", "curious")


def test_non_string_fields_are_skipped():
    fields = scan_partial('{"n": 3, "deep": {"a": [1, 2]}, "text": "x"}')
    assert fields["text"] == ("x", True)
    assert "n" not in fields

"""Reminder times, English + Hinglish, with fixed clocks."""

from datetime import datetime

import pytest

from coco.tools.intents import clean
from coco.tools.when import parse_duration, parse_when, say_duration, say_time

AFTERNOON = datetime(2026, 11, 21, 14, 0)  # a Saturday, 2 pm
NIGHT = datetime(2026, 11, 21, 21, 30)
EARLY = datetime(2026, 11, 21, 5, 0)


@pytest.mark.parametrize(
    ("text", "now", "want"),
    [
        ("remind me at 6 to submit", AFTERNOON, datetime(2026, 11, 21, 18, 0)),
        ("at 6", EARLY, datetime(2026, 11, 21, 6, 0)),
        ("at 6", NIGHT, datetime(2026, 11, 22, 6, 0)),
        ("at 6:30 pm", AFTERNOON, datetime(2026, 11, 21, 18, 30)),
        ("at 18:45", AFTERNOON, datetime(2026, 11, 21, 18, 45)),
        ("at 9 am", AFTERNOON, datetime(2026, 11, 22, 9, 0)),  # 9 am is over: tomorrow
        ("tomorrow at 9", AFTERNOON, datetime(2026, 11, 22, 9, 0)),
        ("tomorrow at 5", AFTERNOON, datetime(2026, 11, 22, 17, 0)),
        ("tonight at 9", AFTERNOON, datetime(2026, 11, 21, 21, 0)),
        ("tomorrow morning", AFTERNOON, datetime(2026, 11, 22, 9, 0)),
        ("at noon", EARLY, datetime(2026, 11, 21, 12, 0)),
        ("on monday at 10", AFTERNOON, datetime(2026, 11, 23, 10, 0)),
        ("6 baje yaad dilana", AFTERNOON, datetime(2026, 11, 21, 18, 0)),
        ("kal subah 9 baje", AFTERNOON, datetime(2026, 11, 22, 9, 0)),
        ("kal shaam 7 baje", AFTERNOON, datetime(2026, 11, 22, 19, 0)),
        ("saade 6 baje", AFTERNOON, datetime(2026, 11, 21, 18, 30)),
        ("paune 5 baje", AFTERNOON, datetime(2026, 11, 21, 16, 45)),
        ("raat 2 baje", AFTERNOON, datetime(2026, 11, 22, 2, 0)),
        ("dedh baje", AFTERNOON, datetime(2026, 11, 22, 1, 30)),
        ("in 20 minutes", AFTERNOON, datetime(2026, 11, 21, 14, 20)),
        ("in an hour", AFTERNOON, datetime(2026, 11, 21, 15, 0)),
        ("in half an hour", AFTERNOON, datetime(2026, 11, 21, 14, 30)),
        ("10 minute baad", AFTERNOON, datetime(2026, 11, 21, 14, 10)),
        ("aadhe ghante mein", AFTERNOON, datetime(2026, 11, 21, 14, 30)),
        ("ek ghante baad", AFTERNOON, datetime(2026, 11, 21, 15, 0)),
        ("set a timer for 5 minutes", AFTERNOON, datetime(2026, 11, 21, 14, 5)),
        ("5 minute ka timer", AFTERNOON, datetime(2026, 11, 21, 14, 5)),
        ("कल सुबह 9 बजे", AFTERNOON, datetime(2026, 11, 22, 9, 0)),  # Devanagari
    ],
)
def test_times(text, now, want):
    got = parse_when(clean(text), now)
    assert got is not None, text
    assert got[0] == want, text


@pytest.mark.parametrize(
    "text", ["buy a new esp32", "remind me to drink water", "i have 2 exams", "do it now"]
)
def test_no_time(text):
    assert parse_when(clean(text), AFTERNOON) is None


def test_span_lets_us_cut_the_time_out():
    text = clean("remind me at 6:30 pm to submit the assignment")
    _when, (a, b) = parse_when(text, AFTERNOON)
    assert text[a:b].strip() == "at 6:30 pm"


def test_saying_it_back():
    assert say_time(datetime(2026, 11, 21, 18, 0), AFTERNOON) == "at 6:00 pm"
    assert say_time(datetime(2026, 11, 22, 9, 0), AFTERNOON) == "tomorrow at 9:00 am"
    assert say_time(datetime(2026, 11, 22, 9, 0), AFTERNOON, "hinglish") == "kal subah 9:00 baje"
    assert say_duration(300) == "5 minutes" and say_duration(5400) == "1 hour 30 min"
    assert parse_duration("in 90 seconds")[0] == 90

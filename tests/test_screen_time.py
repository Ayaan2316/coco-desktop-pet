"""Screen time, break reminders, and what she knows about what you're doing."""

from datetime import datetime

from coco.brain.prompt import PromptContext, build_system_prompt
from coco.core.catdef import load_cat
from coco.screen_time import ScreenTime, describe_minutes

START = datetime(2026, 11, 23, 10, 0).timestamp()


def run(st, seconds, idle=0.0, app="code.exe", start=None):
    """Feed one update per second; returns the events."""
    t0 = start if start is not None else (st._last or START)
    events = []
    for i in range(1, int(seconds) + 1):
        e = st.update(t0 + i, idle, app)
        if e:
            events.append((i, e))
    return events


def make(break_after=600):
    now = [START]
    st = ScreenTime(break_after, clock=lambda: now[0])
    st.update(START, 0, "code.exe")
    return st, now


def test_counts_screen_time_and_asks_once():
    st, _ = make(600)
    events = run(st, 900)
    assert events == [(600, "break_due")]  # once, at 10 minutes
    assert int(st.session) == 900 and int(st.today) == 900
    assert st.kind == "coding" and st.label == "VS Code"


def test_being_away_is_a_break():
    st, _ = make(600)
    run(st, 500)
    run(st, 10, idle=400)  # away for a while (over 5 min idle): a real break
    assert st.session == 0
    assert run(st, 590) == [] and run(st, 20)  # a full new session before the next ask


def test_snooze_and_not_today():
    st, now = make(600)
    run(st, 600)
    now[0] = st._last
    st.snooze(120)
    assert run(st, 100) == [] and run(st, 30)  # asks again after the snooze
    now[0] = st._last
    st.skip_today()
    assert run(st, 3000) == []


def test_never_during_games_videos_or_calls():
    st, _ = make(600)
    assert run(st, 700, app="valorant.exe") == []  # waits...
    assert run(st, 3, app="chrome.exe")  # ...until you're out of the game


def test_new_day_resets_today():
    st, _ = make(600)
    run(st, 300)
    next_day = datetime(2026, 11, 24, 9, 0).timestamp()
    st.update(next_day, 0, "chrome.exe")
    assert st.today == 0


def test_she_knows_what_you_are_doing():
    st, _ = make(3600)
    run(st, 40 * 60)
    assert st.sentence() == "They're coding (VS Code), 40 min at the screen without a break."
    prompt = build_system_prompt(load_cat("coco"), PromptContext(screen=st.sentence()))
    assert "coding (VS Code)" in prompt
    assert describe_minutes(80 * 60) == "1 h 20 min" and describe_minutes(3600) == "1 h"
    unknown = ScreenTime()
    unknown.update(START, 0, "someapp.exe")
    assert unknown.sentence() == "They're using Someapp."  # (no category: just its name)


def test_unknown_apps_and_saving_today(tmp_path):
    st, _ = make(3600)
    st.update(START + 1, 0, "zettlr.exe")
    assert st.sentence() == "They're using Zettlr."
    run(st, 120)
    st.save(tmp_path / "s.json")
    again = ScreenTime(clock=lambda: START + 200)
    again.load(tmp_path / "s.json")
    assert int(again.today) == int(st.today) > 100  # (same day: kept across a restart)

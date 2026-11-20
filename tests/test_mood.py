"""Mood: drift over (fake) time, events, multipliers, sulking, saving + catching up."""

import json

import pytest

from coco.behaviour.lines import LinePicker
from coco.behaviour.mood import Mood, energy_target
from coco.core.config import MoodConfig


def simulate(mood: Mood, seconds: float, step: float = 1.0, **kw) -> None:
    for _ in range(int(seconds / step)):
        mood.update(step, **kw)


def test_boredom_rises_when_ignored():
    m = Mood(boredom=0.2)
    simulate(m, 20 * 60)
    assert m.boredom == pytest.approx(0.2 + 20 * 0.02, abs=0.01)


def test_energy_refills_while_asleep_and_follows_time_of_day_awake():
    m = Mood(energy=0.2)
    simulate(m, 30 * 60, asleep=True)
    assert m.energy > 0.9
    m = Mood(energy=0.9)
    simulate(m, 3 * 3600, hour=2.0)  # awake at 2 am
    assert m.energy < 0.35
    assert energy_target(8) > energy_target(15) > energy_target(20) > energy_target(2)


def test_drift_is_frame_rate_independent():
    a, b = Mood(), Mood()
    simulate(a, 600, step=1 / 60)
    simulate(b, 600, step=1.0)
    for stat in ("happiness", "energy", "affection", "boredom"):
        assert getattr(a, stat) == pytest.approx(getattr(b, stat), abs=0.01)


def test_petting_and_chatting_raise_affection_and_bond():
    m = Mood(affection=0.4, bond=0.3, boredom=0.7)
    m.on_pet()
    assert m.affection == pytest.approx(0.48) and m.bond > 0.3
    m.on_chat("happy")
    assert m.boredom < 0.7 - 0.25
    assert m.since_interaction == 0


def test_throwing_hurts_happiness_proportionally():
    m = Mood(happiness=0.8)
    m.on_thrown(2000)
    assert m.happiness == pytest.approx(0.7)


def test_stats_are_clamped():
    m = Mood(affection=0.99, happiness=0.99)
    for _ in range(20):
        m.on_pet()
    assert m.affection == 1.0 and m.happiness == 1.0
    m.on_thrown(1_000_000)
    assert m.happiness == 0.0


def test_bored_and_tired_multipliers():
    bored = Mood(boredom=0.9, energy=0.6).weight_multipliers()
    assert bored["stalk"] > 1.5 and bored["peek"] > 1.5
    tired = Mood(energy=0.1).weight_multipliers()
    assert tired["sleep"] > 2 and tired["run"] < 1
    zoomies = Mood(energy=0.9).weight_multipliers()
    assert zoomies["run"] == pytest.approx(1.4)


def test_sulks_only_when_ignored_long_with_low_affection():
    m = Mood(affection=0.2)
    assert not m.should_sulk()
    m.since_interaction = 3000
    assert m.should_sulk() and m.weight_multipliers()["sulk"] == 3.0
    assert "missing" in m.words()
    assert m.dominant_emotion() == "sad"
    m.on_pet()
    assert not m.should_sulk()


def test_words_and_emotion():
    assert Mood(happiness=0.9, energy=0.9).words() == "happy, full of energy"
    assert Mood(happiness=0.5, energy=0.5, boredom=0.2, affection=0.5).words() == (
        "calm and content"
    )
    assert Mood(energy=0.1).dominant_emotion() == "sleepy"


def test_save_load_round_trip_and_catch_up(tmp_path):
    path = tmp_path / "mood.json"
    m = Mood(happiness=0.6, energy=0.2, affection=0.9, boredom=0.1, bond=0.5)
    m.save(path, wall_time=1000.0)
    same = Mood.load(path, wall_time=1000.0)
    assert (same.happiness, same.energy, same.bond) == (0.6, 0.2, 0.5)
    later = Mood.load(path, wall_time=1000.0 + 9 * 3600)  # closed overnight
    assert later.energy > 0.9  # she slept
    assert later.boredom > 0.1  # missed you a bit
    assert later.affection < 0.9  # back towards her baseline
    assert later.bond == 0.5  # the bond doesn't fade


def test_load_never_crashes(tmp_path):
    assert Mood.load(tmp_path / "missing.json", 0) == Mood()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", "utf-8")
    assert Mood.load(bad, 0) == Mood()
    weird = tmp_path / "weird.json"
    weird.write_text(json.dumps({"happiness": "lots", "energy": 7, "saved_at": "x"}), "utf-8")
    m = Mood.load(weird, 0)
    assert m.happiness == Mood().happiness and m.energy == 1.0


def test_custom_config_is_used():
    m = Mood(cfg=MoodConfig(boredom_per_min=0.5), boredom=0.0)
    simulate(m, 60)
    assert m.boredom == pytest.approx(0.5, abs=0.01)


# --- canned lines


class FixedRng:
    def __init__(self, value=0.0):
        self.value = value

    def random(self):
        return self.value

    def choice(self, seq):
        return seq[0]


LINES = {"late_night": ["Bed?"], "welcome_back": ["You're back!"]}


def test_late_night_line_needs_night_activity_and_respects_cooldown():
    picker = LinePicker(LINES, cooldown=1200, rng=FixedRng(0.0))
    assert picker.check(0, hour=14, user_idle=0) is None  # daytime
    assert picker.check(0, hour=2, user_idle=600) is None  # 2 am but you're away
    assert picker.check(0, hour=2, user_idle=0) == ("late_night", "Bed?")
    assert picker.check(60, hour=2, user_idle=0) is None  # cooldown
    assert picker.check(1300, hour=2, user_idle=0) == ("late_night", "Bed?")


def test_late_night_is_rare():
    picker = LinePicker(LINES, cooldown=0, rng=FixedRng(0.5))  # dice say no
    assert picker.check(0, hour=2, user_idle=0) is None


def test_welcome_back_after_being_away():
    picker = LinePicker(LINES, cooldown=1200, rng=FixedRng(0.9))
    assert picker.check(0, hour=14, user_idle=700) is None  # away...
    assert picker.check(1, hour=14, user_idle=1) == ("welcome_back", "You're back!")
    assert picker.check(2, hour=14, user_idle=1) is None  # only once

"""Director: availability rules, cooldowns, night sleepiness, pause, cat overrides."""

import random

from coco.behaviour.director import MOVING, Director, Situation
from coco.core.config import BehaviourConfig


def make(**cat_weights):
    return Director(BehaviourConfig(), random.Random(0), cat_weights)


def test_in_bed_only_bed_things():
    d = make()
    names = set(d.candidates(0, Situation("bed")))
    assert "sleep" in names and "hop_out" in names
    assert not names & {"walk", "run", "jump_up", "stalk", "peek", "go_home"}


def test_outside_bed_no_sleep_or_hop_out():
    d = make()
    sit = Situation("ground", can_walk=True, can_jump_up=True, can_peek=True, can_stalk=True)
    names = set(d.candidates(0, sit))
    assert {"walk", "run", "jump_up", "peek", "stalk", "go_home"} <= names
    assert not names & {"sleep", "hop_out"}


def test_impossible_things_are_filtered():
    d = make()
    names = set(d.candidates(0, Situation("ground", can_walk=False)))
    assert not names & {"walk", "run", "jump_up", "peek", "stalk"}


def test_paused_means_no_wandering():
    d = make()
    sit = Situation("ground", can_jump_up=True, can_peek=True, can_stalk=True, paused=True)
    assert not set(d.candidates(0, sit)) & MOVING
    assert not d.allowed("walk", sit)


def test_cooldown_blocks_repeat():
    d = make()
    sit = Situation("ground", can_stalk=True)
    d.last_used["stalk"] = 100.0
    assert "stalk" not in d.candidates(110.0, sit)  # 240 s cooldown
    assert "stalk" in d.candidates(100.0 + 241, sit)


def test_night_makes_her_sleepier():
    d = make()
    day = d.candidates(0, Situation("ground"))
    night = d.candidates(0, Situation("ground", sleepy=True))
    assert night["go_home"] == day["go_home"] * 3
    assert night["walk"] == day["walk"] * 0.5
    assert d.is_night(23.5) and d.is_night(2) and not d.is_night(12)


def test_cat_weight_overrides():
    d = make(stalk=99)
    assert d.weights["stalk"] == 99


def test_choose_records_last_used_and_falls_back_to_sit():
    d = make()
    pick = d.choose(5.0, Situation("ground"))
    assert d.last_used[pick] == 5.0
    d.weights = {}
    assert d.choose(5.0, Situation("ground")) == "sit"

"""Picks what Coco does next when she's idle."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from coco.core.config import BehaviourConfig

MOVING = frozenset({"walk", "run", "go_home", "jump_up", "peek", "stalk", "hop_out"})
SLEEPY = frozenset({"sleep", "go_home"})
BED_ONLY = frozenset({"sleep", "hop_out"})
IN_BED = frozenset({"sit", "loaf", "groom", "yawn", "look_around", "sleep", "hop_out", "sulk"})


@dataclass
class Situation:
    """Facts the Director needs; the engine fills this in from the world snapshot."""

    surface_kind: str  # "ground" | "window" | "bed"
    can_walk: bool = True
    can_jump_up: bool = False
    can_peek: bool = False
    can_stalk: bool = False
    has_bed: bool = True
    sleepy: bool = False  # night time or the user is away
    can_sulk: bool = False  # mood: ignored for too long
    paused: bool = False


class Director:
    def __init__(
        self,
        cfg: BehaviourConfig,
        rng: random.Random,
        cat_weights: dict[str, float] | None = None,
    ) -> None:
        self.cfg = cfg
        self.rng = rng
        self.weights = {**cfg.weights, **(cat_weights or {})}
        self.last_used: dict[str, float] = {}

    def is_night(self, hour: float) -> bool:
        start, end = self.cfg.night_start_hour, self.cfg.night_end_hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end  # wraps past midnight (23 -> 7)

    def on_cooldown(self, activity: str, now: float) -> bool:
        cooldown = self.cfg.cooldowns.get(activity, 0.0)
        return now - self.last_used.get(activity, -math.inf) < cooldown

    def allowed(self, name: str, sit: Situation) -> bool:
        """Is `name` possible in this situation? (Ignores weights and cooldowns.)"""
        if sit.surface_kind == "bed":
            if name not in IN_BED:
                return False
        elif name in BED_ONLY:
            return False
        if sit.paused and name in MOVING:
            return False
        checks = {
            "walk": sit.can_walk,
            "run": sit.can_walk,
            "jump_up": sit.can_jump_up,
            "peek": sit.can_peek,
            "stalk": sit.can_stalk,
            "go_home": sit.has_bed,
            "sulk": sit.can_sulk,
        }
        return checks.get(name, True)

    def candidates(
        self, now: float, sit: Situation, extra: dict[str, float] | None = None
    ) -> dict[str, float]:
        """Activity -> effective weight, after all rules. Exposed for tests/debugging."""
        result: dict[str, float] = {}
        for name, weight in self.weights.items():
            if weight <= 0 or not self.allowed(name, sit) or self.on_cooldown(name, now):
                continue
            if sit.sleepy:
                if name in SLEEPY:
                    weight *= self.cfg.night_sleep_mult
                elif name in MOVING:
                    weight *= self.cfg.night_move_mult
            if extra and name in extra:
                weight *= extra[name]
            result[name] = weight
        return result

    def choose(self, now: float, sit: Situation, extra: dict[str, float] | None = None) -> str:
        options = self.candidates(now, sit, extra)
        if not options:
            return "sit"
        names = list(options)
        pick = self.rng.choices(names, weights=[options[n] for n in names])[0]
        self.last_used[pick] = now
        return pick

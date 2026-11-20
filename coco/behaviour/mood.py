"""Coco's mood: a few stats that drift over time and react to how she's treated."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from coco.core.config import MoodConfig

log = logging.getLogger(__name__)

STATS = ("happiness", "energy", "affection", "boredom", "bond")


def _clamp(x: float) -> float:
    return min(1.0, max(0.0, x))


def _approach(value: float, target: float, dt: float, minutes: float) -> float:
    """Move `value` towards `target`: after `minutes` it has covered ~63 % of the gap.

    (Exponential approach: frame-rate independent and never overshoots.)
    """
    if minutes <= 0:
        return target
    return value + (target - value) * (1 - math.exp(-dt / (minutes * 60)))


def energy_target(hour: float) -> float:
    """How energetic a cat is at this time of day when awake."""
    if 7 <= hour < 11:
        return 0.9  # bright-eyed mornings
    if 11 <= hour < 18:
        return 0.7
    if 18 <= hour < 23:
        return 0.55
    return 0.25  # night owl hours: sleepy


@dataclass
class Mood:
    happiness: float = 0.7
    energy: float = 0.7
    affection: float = 0.5
    boredom: float = 0.2
    bond: float = 0.3
    since_interaction: float = 0.0  # s since you last petted/played/chatted
    cfg: MoodConfig = field(default_factory=MoodConfig, repr=False, compare=False)

    # --- time passing
    def update(
        self,
        dt: float,
        sleepy: bool = False,
        *,
        asleep: bool = False,
        hour: float = 12.0,
        user_active: bool = True,
    ) -> None:
        """Called every frame by the engine."""
        if dt <= 0:
            return
        c = self.cfg
        self.since_interaction += dt
        if asleep:
            self.energy = _approach(self.energy, 1.0, dt, c.energy_sleep_minutes)
            self.boredom = _clamp(self.boredom - 0.5 * c.boredom_per_min * dt / 60)
        else:
            target = energy_target(hour) * (0.85 if sleepy else 1.0)
            self.energy = _approach(self.energy, target, dt, c.energy_awake_minutes)
            # she gets bored faster when you're at the PC but ignoring her
            rate = c.boredom_per_min * (1.0 if user_active else 0.5)
            self.boredom = _clamp(self.boredom + rate * dt / 60)
        baseline = 0.3 + 0.5 * self.bond
        self.affection = _approach(self.affection, baseline, dt, c.affection_minutes)
        mood_target = _clamp(
            0.15 + 0.4 * self.affection + 0.25 * self.energy + 0.3 * (1 - self.boredom)
        )
        self.happiness = _approach(self.happiness, mood_target, dt, c.happiness_minutes)

    # --- events
    def _interacted(self) -> None:
        self.since_interaction = 0.0

    def on_pet(self) -> None:
        c = self.cfg
        self.affection = _clamp(self.affection + c.pet_affection)
        self.happiness = _clamp(self.happiness + c.pet_happiness)
        self.bond = _clamp(self.bond + c.pet_bond)
        self.boredom = _clamp(self.boredom - 0.05)
        self._interacted()

    def on_thrown(self, speed: float = 0.0) -> None:
        """Thrown at `speed` logical px/s: being flung about isn't fun."""
        self.happiness = _clamp(self.happiness - self.cfg.thrown_happiness * speed / 1000)
        self._interacted()

    def on_chat(self, emotion: str = "neutral") -> None:
        c = self.cfg
        self.affection = _clamp(self.affection + c.chat_affection)
        self.bond = _clamp(self.bond + c.chat_bond)
        self.boredom = _clamp(self.boredom - c.play_boredom)
        if emotion in ("happy", "love"):
            self.happiness = _clamp(self.happiness + 0.04)
        elif emotion == "sad":
            self.happiness = _clamp(self.happiness - 0.02)  # she feels for you
        self._interacted()

    def on_play(self) -> None:
        """She pounced on your cursor (or, games)."""
        self.boredom = _clamp(self.boredom - self.cfg.play_boredom)
        self.energy = _clamp(self.energy - 0.03)
        self.happiness = _clamp(self.happiness + 0.03)

    # --- outputs
    def should_sulk(self) -> bool:
        c = self.cfg
        return self.affection < c.sulk_affection and self.since_interaction > c.sulk_after

    def weight_multipliers(self) -> dict[str, float]:
        """How the Director should bias her choices right now (1.0 = no change)."""
        c = self.cfg
        m: dict[str, float] = {}

        def mult(names: tuple[str, ...], k: float) -> None:
            for n in names:
                m[n] = m.get(n, 1.0) * k

        if self.boredom > c.bored_at:  # bored: go make some fun
            k = 1 + 2 * (self.boredom - c.bored_at) / (1 - c.bored_at)
            mult(("stalk", "peek", "run", "walk", "jump_up"), k)
        if self.energy < c.tired_at:  # tired: nap time
            k = 1 + 2 * (c.tired_at - self.energy) / c.tired_at
            mult(("sleep", "loaf", "go_home"), k)
            mult(("run", "stalk", "peek", "jump_up"), 1 / k)
        elif self.energy > 0.8:  # zoomies
            mult(("run", "walk", "stalk", "jump_up"), 1.4)
        m["sulk"] = 3.0 if self.should_sulk() else 0.0
        return m

    def words(self) -> str:
        """A few words for the AI prompt: 'Right now you feel <words>.'"""
        parts = []
        if self.happiness > 0.75:
            parts.append("happy")
        elif self.happiness < 0.35:
            parts.append("a bit down")
        if self.energy < 0.3:
            parts.append("sleepy")
        elif self.energy > 0.8:
            parts.append("full of energy")
        if self.boredom > self.cfg.bored_at:
            parts.append("bored")
        if self.should_sulk():
            parts.append("ignored and missing your human")
        elif self.affection > 0.75:
            parts.append("very affectionate")
        return ", ".join(parts) or "calm and content"

    def dominant_emotion(self) -> str:
        if self.should_sulk() or self.happiness < 0.3:
            return "sad"
        if self.energy < 0.25:
            return "sleepy"
        if self.affection > 0.8:
            return "love"
        if self.happiness > 0.75:
            return "happy"
        if self.boredom > self.cfg.bored_at:
            return "curious"
        return "neutral"

    # --- persistence
    def to_dict(self, wall_time: float) -> dict:
        data = {name: round(getattr(self, name), 4) for name in STATS}
        data["since_interaction"] = round(self.since_interaction, 1)
        data["saved_at"] = wall_time
        return data

    def save(self, path: Path, wall_time: float) -> None:
        try:
            path.write_text(json.dumps(self.to_dict(wall_time), indent=2), "utf-8")
        except OSError:
            log.exception("Could not save mood to %s", path)

    @classmethod
    def load(cls, path: Path, wall_time: float, cfg: MoodConfig | None = None) -> Mood:
        """Load from `path` and catch up on the time Coco was closed. Never crashes:
        a missing or broken file just gives a fresh mood."""
        mood = cls(cfg=cfg or MoodConfig())
        try:
            data = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            return mood
        except (OSError, ValueError):
            log.warning("Mood file %s is unreadable; starting fresh", path)
            return mood
        for name in STATS:
            value = data.get(name)
            if isinstance(value, int | float):
                setattr(mood, name, _clamp(float(value)))
        since = data.get("since_interaction", 0.0)
        mood.since_interaction = float(since) if isinstance(since, int | float) else 0.0
        saved_at = data.get("saved_at")
        if isinstance(saved_at, int | float):
            mood.catch_up(max(0.0, min(wall_time - saved_at, 7 * 24 * 3600)))
        return mood

    def catch_up(self, seconds: float) -> None:
        """Time passed while Coco was closed: she napped (energy back) but missed you
        a little (boredom up, affection back to baseline)."""
        if seconds <= 0:
            return
        c = self.cfg
        nap = min(seconds, 8 * 3600)
        self.energy = _approach(self.energy, 1.0, nap, c.energy_sleep_minutes)
        self.boredom = _clamp(self.boredom + min(0.4, c.boredom_per_min * seconds / 60 * 0.25))
        self.affection = _approach(
            self.affection, 0.3 + 0.5 * self.bond, seconds, c.affection_minutes
        )
        self.since_interaction += seconds

"""Pre-written lines Coco says on her own (no AI)."""

from __future__ import annotations

import random

WELCOME_AWAY = 600.0  # s you must have been away for a "welcome back"


class LinePicker:
    def __init__(
        self,
        lines: dict[str, list[str] | str],
        cooldown: float,
        rng: random.Random | None = None,
        chance_per_check: float = 1 / 900,
    ) -> None:
        self.lines = lines
        self.cooldown = cooldown
        self.rng = rng or random.Random()
        self.chance = chance_per_check  # for "ambient" lines like late_night
        self.last_said = -1e12
        self._was_away = False

    def _pick(self, kind: str, now: float) -> str | None:
        options = self.lines.get(kind)
        if not options or now - self.last_said < self.cooldown:
            return None
        if isinstance(options, str):
            options = [options]
        self.last_said = now
        return self.rng.choice(options)

    def check(self, now: float, hour: float, user_idle: float) -> tuple[str, str] | None:
        """Call about once a second. Returns (kind, text) when she should say something."""
        # Welcome back: you were away for a while and just moved the mouse / typed.
        if user_idle > WELCOME_AWAY:
            self._was_away = True
            return None
        if self._was_away and user_idle < 5:
            self._was_away = False
            text = self._pick("welcome_back", now)
            return ("welcome_back", text) if text else None
        # Still up in the middle of the night (and actually at the PC)?
        if 1 <= hour < 4 and user_idle < 60 and self.rng.random() < self.chance:
            text = self._pick("late_night", now)
            return ("late_night", text) if text else None
        return None

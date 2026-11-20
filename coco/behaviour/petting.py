"""Detects petting: slow back-and-forth cursor movement over Coco."""

from __future__ import annotations

import math
from collections import deque

from coco.core.config import PettingConfig


class PetDetector:
    def __init__(self, cfg: PettingConfig, scale: float = 1.0) -> None:
        self.cfg = cfg
        self.scale = scale  # logical -> physical px
        self._samples: deque[tuple[float, float, float]] = deque()
        self.last_pet = -math.inf

    def add_sample(self, t: float, x: float, y: float) -> bool:
        """Feed one cursor sample. Returns True when a pet gesture just completed."""
        cfg, k = self.cfg, self.scale
        if self._samples and t - self._samples[-1][0] > 0.3:
            self._samples.clear()  # the cursor paused or left her: start over
        self._samples.append((t, x, y))
        while self._samples and t - self._samples[0][0] > cfg.window:
            self._samples.popleft()

        path = 0.0
        reversals = 0
        last_dir = 0
        pts = list(self._samples)
        for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:], strict=False):
            dt = t1 - t0
            if dt <= 0:
                continue
            dist = math.hypot(x1 - x0, y1 - y0)
            speed = dist / dt
            if not (cfg.min_speed * k <= speed <= cfg.max_speed * k):
                continue
            path += dist
            dx = x1 - x0
            if abs(dx) >= 2 * k:
                direction = 1 if dx > 0 else -1
                if last_dir and direction != last_dir:
                    reversals += 1
                last_dir = direction

        if path >= cfg.min_path * k and reversals >= cfg.min_reversals:
            self.last_pet = t
            self._samples.clear()  # keep rubbing to pet again
            return True
        return False

    def is_petting(self, now: float) -> bool:
        """True while the purr should continue (a pet happened within `linger` seconds)."""
        return now - self.last_pet <= self.cfg.linger

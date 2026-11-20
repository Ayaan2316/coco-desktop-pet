"""Test helpers: a headless simulator (fake desktop, real sensors, real engine).

Time is stepped by hand, the RNG is seeded and night mode is off, so runs are deterministic.
"""

from __future__ import annotations

import random

import pytest
import yaml

from coco.behaviour.engine import BehaviourEngine, CatPose
from coco.behaviour.world import place_bed
from coco.core.anims import load_manifest
from coco.core.catdef import load_cat
from coco.core.config import Config
from coco.core.events import Event
from coco.core.geometry import Point, Surface
from coco.core.paths import ASSETS_DIR
from coco.desktop.base import WindowInfo
from coco.desktop.fake import FakeDesktop
from coco.desktop.sensors import DesktopSensor

GROUND_Y = 1032  # FakeDesktop: 1920x1080 monitor with a 48 px taskbar at the bottom
OFFSCREEN = Point(-5000, -5000)  # cursor far away (no stalking)


class Sim:
    def __init__(
        self,
        windows: list[WindowInfo] | None = None,
        *,
        start_in_bed: bool = False,
        foreground: int | None = None,
        cursor: Point = OFFSCREEN,
        seed: int = 1,
        idle_forever: bool = True,
    ) -> None:
        self.cfg = Config()
        b = self.cfg.behaviour
        b.night_start_hour = b.night_end_hour = 0  # never night: deterministic
        b.start_in_bed = start_in_bed
        if idle_forever:  # she won't wander off on her own in the middle of a test
            b.decision_interval = [10_000, 10_000]
        self.t = 0.0
        self.fake = FakeDesktop(windows=list(windows or []), foreground=foreground, cursor=cursor)
        self.sensor = DesktopSensor(self.fake, self.cfg, clock=lambda: self.t)
        meta = yaml.safe_load((ASSETS_DIR / "bed" / "bed.yaml").read_text("utf-8"))
        self.sensor.bed = place_bed(meta, self.sensor.monitor.work, 1.0, 128)
        cat = load_cat("coco")
        self.eng = BehaviourEngine(
            self.cfg,
            cat,
            load_manifest(cat.manifest_path),
            rng=random.Random(seed),
            clock=lambda: self.t,
        )
        self.seen: list[str] = []
        self.pose: CatPose = self.step()

    def world(self):
        return self.sensor.snapshot(self.eng.frame_px, self.eng.support_hwnd)

    def step(self, dt: float = 1 / 60) -> CatPose:
        self.t += dt
        self.pose = self.eng.tick(dt, self.world())
        name = self.eng.state.name
        if not self.seen or self.seen[-1] != name:
            self.seen.append(name)
        return self.pose

    def run(self, seconds: float, until=None, dt: float = 1 / 60) -> bool:
        """Step for up to `seconds`; stop early (returning True) once until(sim) is true."""
        for _ in range(int(seconds / dt)):
            self.step(dt)
            if until is not None and until(self):
                return True
        return False

    def send(self, topic: str, payload=None) -> None:
        self.eng.handle(Event(topic, payload, timestamp=self.t))

    @property
    def state(self) -> str:
        return self.eng.state.name

    def stand_on(self, surface_id, x: float) -> Surface:
        """Teleport her onto a surface (sitting) - test setup shortcut."""
        from coco.behaviour.states import Sit

        s = next(s for s in self.world().surfaces if s.id == surface_id)
        self.eng.body.x, self.eng.body.y = x, s.y
        self.eng.surface = s
        self.eng._switch(Sit(self.eng), clear_plan=True)
        self.step()
        return s


@pytest.fixture
def sim_factory():
    return Sim

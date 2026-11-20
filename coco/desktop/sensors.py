"""Turns desktop readings into a world snapshot each frame."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from coco.behaviour.surfaces import build_surfaces
from coco.behaviour.world import BedGeom, WorldSnapshot
from coco.core.bus import EventBus
from coco.core.config import Config
from coco.core.events import Event, Topic
from coco.core.geometry import Rect, Surface
from coco.desktop.base import DesktopAPI, MonitorInfo, WindowInfo

log = logging.getLogger(__name__)


def pick_monitor(monitors: list[MonitorInfo], wanted: str) -> MonitorInfo:
    if wanted and wanted not in ("primary", "follow"):
        for m in monitors:
            if m.device.lower() == wanted.lower():
                return m
        log.warning("Monitor %r not found, using the primary one", wanted)
    return next((m for m in monitors if m.primary), monitors[0])


def monitor_containing(monitors: list[MonitorInfo], rect: Rect) -> MonitorInfo | None:
    """The monitor holding most of a window (its centre decides)."""
    cx, cy = (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2
    for m in monitors:
        if m.rect.left <= cx < m.rect.right and m.rect.top <= cy < m.rect.bottom:
            return m
    return None


class MonitorFollower:
    """ "Follow me": she moves to the screen you're working on once you've been there
    `delay` seconds (not for a quick glance), then waits `cooldown` before moving again."""

    def __init__(self, delay: float = 4.0, cooldown: float = 15.0) -> None:
        self.delay, self.cooldown = delay, cooldown
        self._since: float | None = None
        self._target = ""
        self._moved_at = -1e9

    def update(self, now: float, current: str, active: str | None) -> str | None:
        if not active or active == current:
            self._since, self._target = None, ""
            return None
        if active != self._target:
            self._target, self._since = active, now
            return None
        if now - (self._since or now) >= self.delay and now - self._moved_at >= self.cooldown:
            self._moved_at, self._since, self._target = now, None, ""
            return active
        return None


def virtual_rect(monitors: list[MonitorInfo]) -> Rect:
    return Rect(
        min(m.rect.left for m in monitors),
        min(m.rect.top for m in monitors),
        max(m.rect.right for m in monitors),
        max(m.rect.bottom for m in monitors),
    )


class DesktopSensor:
    def __init__(
        self,
        api: DesktopAPI,
        cfg: Config,
        bus: EventBus | None = None,
        own_pids: frozenset[int] = frozenset(),
        clock=time.monotonic,
    ) -> None:
        self.api = api
        self.cfg = cfg
        self.bus = bus
        self.own_pids = own_pids
        self.clock = clock
        self.bed: BedGeom | None = None
        self.windows: list[WindowInfo] = []
        self.surfaces: list[Surface] = []
        self._surface_key: tuple | None = None
        self._next_scan = 0.0
        self._idle = 0.0
        self._next_idle = 0.0
        self._foreground: int | None = None
        self.fullscreen = False
        self._fs_clear_since: float | None = None
        self.home = cfg.ui.home_monitor  # "primary" | "follow" | a device (dashboard setting)
        self.monitor: MonitorInfo | None = None
        self.refresh_monitors()

    # --- monitors
    def refresh_monitors(self) -> None:
        self.monitors = self.api.monitors()
        old = self.monitor.device if self.monitor is not None else ""
        still_there = next((m for m in self.monitors if m.device == old), None)
        if self.home == "follow" and still_there is not None:
            self.monitor = still_there  # following you: stay where she is, if it's still there
        else:
            self.monitor = pick_monitor(self.monitors, self.home)
        self.virtual = virtual_rect(self.monitors)
        self.refresh_surfaces()

    def refresh_surfaces(self) -> None:
        """Rebuild the windows / ground she can stand on (e.g. after moving screens)."""
        self._surface_key = None  # force a surface rebuild
        self._next_scan = 0.0

    # --- per frame
    def snapshot(
        self,
        frame_px: float,
        support_hwnd: int | None = None,
        sleeping: bool = False,
    ) -> WorldSnapshot:
        now = self.clock()
        sc = self.cfg.sensors
        if now >= self._next_scan:
            self.windows = self.api.top_level_windows(self.own_pids)
            self._next_scan = now + (
                sc.idle_window_poll_interval if sleeping else sc.window_poll_interval
            )
        elif support_hwnd is not None:
            # Re-check the window under her paws every frame, so she falls the moment
            # it moves, closes or gets minimised.
            fresh = self.api.window_info(support_hwnd)
            if fresh is None:
                self.windows = [w for w in self.windows if w.hwnd != support_hwnd]
            else:
                self.windows = [fresh if w.hwnd == support_hwnd else w for w in self.windows]

        key = (tuple(self.windows), self.monitor, self.bed, round(frame_px))
        if key != self._surface_key:
            self.surfaces = build_surfaces(
                self.windows,
                self.monitor.rect,
                self.monitor.work,
                min_width=frame_px * 0.45,
                headroom=frame_px * 0.7,
                bed=self.bed,
            )
            self._surface_key = key

        if now >= self._next_idle:
            self._idle = self.api.user_idle_seconds()
            self._next_idle = now + 1.0

        # The "active window" she may jump onto: the last foreground window that is a real
        # app window. Clicking her tray menu or the desktop doesn't make her forget it.
        fg = self.api.foreground_window()
        known = {w.hwnd for w in self.windows}
        if fg in known:
            self._foreground = fg
        elif self._foreground not in known:
            self._foreground = None

        local = datetime.now()
        return WorldSnapshot(
            monitor=self.monitor.rect,
            work=self.monitor.work,
            virtual=self.virtual,
            scale=self.monitor.scale,
            surfaces=self.surfaces,
            foreground=self._foreground,
            cursor=self.api.cursor_pos(),
            user_idle=self._idle,
            hour=local.hour + local.minute / 60,
            bed=self.bed,
        )

    def active_window(self):
        """The last real app window you used (fresh position), or None."""
        if self._foreground is None:
            return None
        return self.api.window_info(self._foreground)

    # --- polite mode
    def poll_fullscreen(self) -> bool:
        """Call ~1x/s. Publishes Topic.FULLSCREEN when the state flips (with a resume delay
        so she doesn't flicker back between two fullscreen videos)."""
        pc = self.cfg.polite
        now = self.clock()
        raw = pc.enabled and self.api.fullscreen_app_active()
        if raw:
            self._fs_clear_since = None
            target = True
        else:
            if self._fs_clear_since is None:
                self._fs_clear_since = now
            target = self.fullscreen and (now - self._fs_clear_since < pc.resume_delay)
        if target != self.fullscreen:
            self.fullscreen = target
            log.info("Fullscreen app %s", "detected: hiding" if target else "gone: coming back")
            if self.bus is not None:
                self.bus.publish(Event(Topic.FULLSCREEN, target))
        return self.fullscreen

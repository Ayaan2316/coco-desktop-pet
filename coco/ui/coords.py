"""Converts physical pixels to Qt logical pixels."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QGuiApplication, QScreen

from coco.desktop.base import MonitorInfo


class CoordMapper:
    def __init__(self, monitors: list[MonitorInfo]) -> None:
        self.refresh(monitors)

    def refresh(self, monitors: list[MonitorInfo]) -> None:
        self.monitors = monitors
        self._screens: dict[str, QScreen] = {}
        screens = QGuiApplication.screens()
        for m in monitors:
            match = next((s for s in screens if s.name() == m.device), None)
            if match is None:  # fall back to matching the top-left corner
                match = next(
                    (
                        s
                        for s in screens
                        if s.geometry().x() == m.rect.left and s.geometry().y() == m.rect.top
                    ),
                    QGuiApplication.primaryScreen(),
                )
            self._screens[m.device] = match

    def monitor_at(self, x: float, y: float) -> MonitorInfo:
        for m in self.monitors:
            if m.rect.contains(x, y):
                return m

        def dist(m: MonitorInfo) -> float:
            cx = min(max(x, m.rect.left), m.rect.right)
            cy = min(max(y, m.rect.top), m.rect.bottom)
            return (cx - x) ** 2 + (cy - y) ** 2

        return min(self.monitors, key=dist)

    def screen_for(self, monitor: MonitorInfo) -> QScreen:
        return self._screens.get(monitor.device) or QGuiApplication.primaryScreen()

    def to_logical(self, x: float, y: float, monitor: MonitorInfo | None = None) -> QPointF:
        """Convert a physical point. Pass `monitor` to use a fixed monitor's mapping (keeps
        a window that straddles two monitors in one coordinate system)."""
        m = monitor or self.monitor_at(x, y)
        screen = self.screen_for(m)
        g = screen.geometry()
        dpr = screen.devicePixelRatio()
        return QPointF(g.x() + (x - m.rect.left) / dpr, g.y() + (y - m.rect.top) / dpr)

    def dpr_for(self, monitor: MonitorInfo) -> float:
        return self.screen_for(monitor).devicePixelRatio()

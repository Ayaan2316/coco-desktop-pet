"""Debug overlay (Ctrl+Alt+D)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from coco.desktop.base import MonitorInfo
from coco.ui.coords import CoordMapper

COLORS = {
    "ground": QColor(60, 220, 120),
    "window": QColor(80, 160, 255),
    "foreground": QColor(255, 210, 60),
    "bed": QColor(255, 150, 60),
    "cat": QColor(255, 90, 170),
    "cursor": QColor(255, 255, 255),
}


class DebugOverlay(QWidget):
    def __init__(self, mapper: CoordMapper, info: Callable[[], dict]) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Coco debug overlay")
        self.mapper = mapper
        self.info = info  # callable returning the data to draw (see CocoApp.debug_info)
        self.monitor: MonitorInfo | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.update)

    def cover(self, monitor: MonitorInfo) -> None:
        self.monitor = monitor
        g = self.mapper.screen_for(monitor).geometry()
        self.setGeometry(g)

    def set_active(self, on: bool) -> None:
        if on:
            self.show()
            self.timer.start()
        else:
            self.timer.stop()
            self.hide()

    def _pt(self, x: float, y: float) -> QPointF:
        p = self.mapper.to_logical(x, y, self.monitor)
        return QPointF(p.x() - self.x(), p.y() - self.y())

    def paintEvent(self, event) -> None:  # noqa: N802
        data = self.info()
        if not data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        world = data.get("world")
        if world is not None:
            for s in world.surfaces:
                kind = s.kind
                if kind == "window" and s.id == world.foreground:
                    kind = "foreground"
                p.setPen(QPen(COLORS[kind], 3))
                p.drawLine(self._pt(s.x1, s.y), self._pt(s.x2, s.y))
            if world.bed is not None:
                r = world.bed.rect
                p.setPen(QPen(COLORS["bed"], 1, Qt.PenStyle.DashLine))
                p.drawRect(QRectF(self._pt(r.left, r.top), self._pt(r.right, r.bottom)))
            c = self._pt(world.cursor.x, world.cursor.y)
            p.setPen(QPen(COLORS["cursor"], 1))
            p.drawEllipse(c, 6, 6)

        body = data.get("body_box")
        if body is not None:
            left, top, right, bottom, ax, ay = body
            p.setPen(QPen(COLORS["cat"], 2))
            p.drawRect(QRectF(self._pt(left, top), self._pt(right, bottom)))
            p.setBrush(COLORS["cat"])
            p.drawEllipse(self._pt(ax, ay), 4, 4)
            p.setBrush(Qt.BrushStyle.NoBrush)

        lines = data.get("lines", [])
        p.setFont(QFont("Consolas", 9))
        fm = p.fontMetrics()
        width = max((fm.horizontalAdvance(t) for t in lines), default=0) + 16
        height = fm.height() * len(lines) + 12
        panel = QRectF(12, 12, width, height)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 24, 32, 210))
        p.drawRoundedRect(panel, 6, 6)
        p.setPen(QColor(235, 240, 250))
        for i, text in enumerate(lines):
            p.drawText(QPointF(20, 18 + fm.ascent() + i * fm.height()), text)
        p.end()

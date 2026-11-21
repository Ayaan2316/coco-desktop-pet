"""The "Coco is driving" chip shown while the agent works."""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter

from coco.ui.focus_chip import FocusChip


class DrivingChip(FocusChip):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Coco driving")
        self.font_ = QFont("Segoe UI", 10, QFont.Weight.Bold)

    def show_on(self, screen_geometry: QRect, text: str = "") -> None:
        self.set_text(text or "🐾 Coco is driving · Esc to stop", on_break=False)
        self.setFixedSize(self.width() + 14, self.height() + 6)
        self.move(screen_geometry.center().x() - self.width() // 2, screen_geometry.top() + 10)
        self.show()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(226, 82, 104, 240))
        p.setPen(QColor(255, 255, 255, 220))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(self.font_)
        p.setPen(QColor(255, 255, 255))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)

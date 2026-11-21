"""The focus-mode countdown next to Coco's head."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from coco.desktop import windows as win


class FocusChip(QWidget):
    def __init__(self) -> None:
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
        self.setWindowTitle("Coco focus")
        self.font_ = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        self.text = ""
        self.on_break = False

    def hwnd(self) -> int:
        return int(self.winId())

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        win.make_unfocusable(self.hwnd())

    def set_text(self, text: str, on_break: bool) -> None:
        if text == self.text and on_break == self.on_break:
            return
        self.text, self.on_break = text, on_break
        fm = QFontMetrics(self.font_)
        self.setFixedSize(fm.horizontalAdvance(text) + 18, fm.height() + 8)
        self.update()

    def follow(self, head: QPointF) -> None:
        """Sit just right of her head."""
        self.move(int(head.x() + 22), int(head.y() - self.height() - 6))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(214, 240, 222, 235) if self.on_break else QColor(255, 236, 228, 240)
        p.setBrush(bg)
        p.setPen(QColor(150, 140, 130))
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setFont(self.font_)
        p.setPen(QColor(58, 52, 64))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)

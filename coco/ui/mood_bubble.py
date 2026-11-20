"""Small mood icons above Coco's head."""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from coco.core.paths import ASSETS_DIR
from coco.desktop import windows as win

ICON_PX = 30  # logical size of the icon
POP_TIME = 0.3  # s for the pop-in / fade-out


class MoodBubble(QWidget):
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
        self.setWindowTitle("Coco mood")
        self.setFixedSize(ICON_PX + 8, ICON_PX + 18)
        self._sources = {
            p.stem: QPixmap(str(p)) for p in sorted((ASSETS_DIR / "bubbles").glob("*.png"))
        }
        self._scaled: dict[str, QPixmap] = {}
        self._dpr = 0.0
        self.icon: str | None = None
        self._shown_icon: str | None = None
        self._changed_at = 0.0
        self._opacity = 0.0
        self._rise = 0.0
        self.anim = QTimer(self)  # only runs during pop-in / fade-out
        self.anim.setInterval(33)
        self.anim.timeout.connect(self._animate)

    def hwnd(self) -> int:
        return int(self.winId())

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        win.make_unfocusable(self.hwnd())

    def _pixmap(self, name: str) -> QPixmap | None:
        dpr = self.devicePixelRatioF()
        if dpr != self._dpr:
            self._scaled.clear()
            self._dpr = dpr
        if name not in self._scaled and name in self._sources:
            size = round(ICON_PX * dpr)
            pm = self._sources[name].scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pm.setDevicePixelRatio(dpr)
            self._scaled[name] = pm
        return self._scaled.get(name)

    def update_icon(self, icon: str | None, head: QPointF, facing: int) -> None:
        """Call every frame with the pose's icon and her head position (logical px)."""
        now = time.monotonic()
        if icon != self.icon:
            self.icon = icon
            self._changed_at = now
            if icon is not None:
                self._shown_icon = icon
                self.show()
            self.anim.start()
        if self._shown_icon is None:
            return
        # Float just above her head, a little towards the back of it.
        x = head.x() + (14 if facing < 0 else -14) - self.width() / 2
        bob = 0.0
        y = head.y() - self.height() - 6 - self._rise + bob
        pos = QPointF(x, y).toPoint()
        if pos != self.pos():
            self.move(pos)

    def _animate(self) -> None:
        t = min((time.monotonic() - self._changed_at) / POP_TIME, 1.0)
        if self.icon is not None:  # popping in
            self._opacity = t
            self._rise = 6 * t
        else:  # fading out, drifting up
            self._opacity = 1 - t
            self._rise = 6 + 8 * t
        self.update()
        if t >= 1.0:
            self.anim.stop()
            if self.icon is None:
                self._shown_icon = None
                self.hide()

    def hide_now(self) -> None:
        self.anim.stop()
        self.icon = self._shown_icon = None
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._shown_icon is None:
            return
        pm = self._pixmap(self._shown_icon)
        if pm is None:
            return
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setOpacity(self._opacity)
        p.drawPixmap(4, self.height() - ICON_PX - 2, pm)
        p.end()

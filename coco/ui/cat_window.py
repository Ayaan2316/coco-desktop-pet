"""Coco's transparent, frameless, always-on-top window."""

from __future__ import annotations

import logging
import math

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

from coco.behaviour.engine import CatPose
from coco.core.bus import EventBus
from coco.core.events import Event, Topic
from coco.desktop import windows as win
from coco.desktop.base import DesktopAPI, MonitorInfo
from coco.ui.coords import CoordMapper
from coco.ui.sprites import Frame, SpriteLibrary

log = logging.getLogger(__name__)

DRAG_THRESHOLD = 6  # logical px the mouse must move (button held) before it's a drag


class CatWindow(QWidget):
    def __init__(
        self,
        bus: EventBus,
        sprites: SpriteLibrary,
        mapper: CoordMapper,
        desktop: DesktopAPI,
        click_mask: bool = True,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)  # hover events (for petting) without a button held
        self.setWindowTitle("Coco")

        self.bus = bus
        self.sprites = sprites
        self.mapper = mapper
        self.desktop = desktop
        self.click_mask = click_mask
        self.monitor: MonitorInfo | None = None  # whose coordinate mapping we use
        self.hotkey_handler = None  # set by the app: callable(hotkey_id)

        self._frame: Frame | None = None
        self._frame_key: tuple | None = None
        self._click_through = False
        self._press_pos: QPoint | None = None
        self._dragging = False
        self.last_pose: CatPose | None = None

    # --- native setup
    def hwnd(self) -> int:
        return int(self.winId())

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        win.make_unfocusable(self.hwnd())

    # --- drawing
    def show_pose(self, pose: CatPose) -> None:
        self.last_pose = pose
        key = (pose.anim, pose.frame, pose.facing > 0)
        if key != self._frame_key:
            self._frame = self.sprites.frame(*key)
            self._frame_key = key
            if self.size().width() != self._frame.width or self.height() != self._frame.height:
                self.resize(self._frame.width, self._frame.height)
            if self.click_mask:
                self.setMask(self._frame.mask)
            self.update()

        f = self._frame
        p = self.mapper.to_logical(pose.x, pose.y, self.monitor)
        top_left = QPoint(
            math.floor(p.x() - f.anchor.x() + 0.5), math.floor(p.y() - f.anchor.y() + 0.5)
        )
        if top_left != self.pos():
            self.move(top_left)

        if pose.click_through != self._click_through:
            self._click_through = pose.click_through
            win.set_click_through(self.hwnd(), pose.click_through)

    def invalidate(self) -> None:
        """Forget the cached frame (after a size/DPI change)."""
        self._frame_key = None

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._frame is None:
            return
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawPixmap(0, 0, self._frame.pixmap)
        painter.end()

    # --- mouse
    def _cursor(self):
        return self.desktop.cursor_pos()  # physical px, same units as the engine

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._dragging = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton and self._press_pos is not None:
            if not self._dragging:
                moved = event.globalPosition().toPoint() - self._press_pos
                if moved.manhattanLength() >= DRAG_THRESHOLD:
                    self._dragging = True
                    self.bus.publish(Event(Topic.DRAG_START))
        elif not event.buttons():
            self.bus.publish(Event(Topic.HOVER, self._cursor()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._dragging:
            self.bus.publish(Event(Topic.DRAG_END))
        elif self._press_pos is not None:
            self.bus.publish(Event(Topic.CLICK, self._cursor()))
        self._press_pos = None
        self._dragging = False

    # --- hotkeys
    def nativeEvent(self, event_type, message):  # noqa: N802
        if event_type == b"windows_generic_MSG" or event_type == "windows_generic_MSG":
            hotkey = win.hotkey_id_from_message(int(message))
            if hotkey is not None and self.hotkey_handler is not None:
                self.hotkey_handler(hotkey)
                return True, 0
        return False, 0

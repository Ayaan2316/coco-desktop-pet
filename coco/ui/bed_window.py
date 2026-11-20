"""Coco's hammock, drawn as two click-through layers (behind and in front of her)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from coco.behaviour.world import BedGeom
from coco.desktop import windows as win
from coco.desktop.base import MonitorInfo
from coco.ui.coords import CoordMapper


class BedLayer(QWidget):
    def __init__(self, image_path: Path, name: str) -> None:
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
        self.setWindowTitle(f"Coco bed ({name})")
        self.source = QImage(str(image_path)).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        self.pixmap: QPixmap | None = None

    def hwnd(self) -> int:
        return int(self.winId())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        win.make_unfocusable(self.hwnd())

    def load(self, image_path: Path) -> None:
        """Swap in new bed art (Reload config + art). Call place() afterwards."""
        self.source = QImage(str(image_path)).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )

    def place(self, geom: BedGeom, mapper: CoordMapper, monitor: MonitorInfo) -> None:
        dpr = mapper.dpr_for(monitor)
        top_left = mapper.to_logical(geom.rect.left, geom.rect.top, monitor)
        w_log = max(1, round(geom.rect.width / dpr))
        h_log = max(1, round(geom.rect.height / dpr))
        img = self.source.scaled(
            round(geom.rect.width),
            round(geom.rect.height),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.pixmap = QPixmap.fromImage(img)
        self.pixmap.setDevicePixelRatio(dpr)
        self.setGeometry(round(top_left.x()), round(top_left.y()), w_log, h_log)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.pixmap is None:
            return
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawPixmap(QPoint(0, 0), self.pixmap)
        painter.end()

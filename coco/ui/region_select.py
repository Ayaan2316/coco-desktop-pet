"""Drag-to-select a screen area for screenshots."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class RegionSelect(QWidget):
    def __init__(self, on_done) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setWindowTitle("Coco: drag an area (Esc = cancel)")
        self.on_done = on_done
        self.screen_ = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.setGeometry(self.screen_.geometry())
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self._finished = False

    def start(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def cancel(self) -> None:
        self._finish(None)

    def _finish(self, rect: QRect | None) -> None:
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.on_done(rect, self.screen_ if rect is not None else None)
        self.close()

    def _rect(self) -> QRect:
        return QRect(self._start, self._end).normalized()

    # --- Qt events
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._finish(None)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._finish(None)
            return
        self._start = self._end = event.position().toPoint()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._start is not None:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._start is None:
            return
        self._end = event.position().toPoint()
        r = self._rect()
        if r.width() < 6 or r.height() < 6:  # a click, not a drag
            self._start = self._end = None
            self.update()
            return
        self._finish(r.translated(self.geometry().topLeft()))  # -> global logical px

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 18, 30, 90))
        if self._start is not None and self._end is not None:
            r = self._rect()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(r, Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(255, 255, 255, 230), 2, Qt.PenStyle.DashLine))
            p.drawRect(r)
        else:
            p.setPen(QColor(255, 255, 255, 220))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Drag to choose an area   (Esc = cancel)")  # fmt: skip

"""Coco's speech bubble: ask a question, read the answer as it streams."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from coco.brain.schemas import BrainReply, BrainStatus
from coco.core.bus import EventBus
from coco.core.config import ChatUIConfig
from coco.core.events import Event, Topic
from coco.desktop import windows as win

log = logging.getLogger(__name__)

PAD = 12  # inner padding (logical px)
TAIL = 12  # tail height
RADIUS = 14
BG = QColor(255, 253, 248, 248)
BORDER = QColor(196, 186, 170)
TEXT = "#3a3440"


class SpeechBubble(QWidget):
    def __init__(self, bus: EventBus, cfg: ChatUIConfig, cat_name: str = "Coco") -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(f"{cat_name} says")
        self.bus = bus
        self.cfg = cfg
        self.cat_name = cat_name
        self.owner_name = "you"  # set by the app (config.yaml owner_name)

        self.view = QTextBrowser(self)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setOpenExternalLinks(True)
        self.view.setStyleSheet(f"background: transparent; color: {TEXT};")
        self.view.setFont(QFont("Segoe UI", 10))
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.input = QLineEdit(self)
        self.input.setPlaceholderText(f"Ask {cat_name}...  (Enter to send, Esc to close)")
        self.input.setFont(QFont("Segoe UI", 10))
        self.input.setStyleSheet(
            "QLineEdit { background: #f4efe6; border: 1px solid #d8cfc0; border-radius: 8px;"
            f" padding: 4px 8px; color: {TEXT}; }}"
        )
        self.input.returnPressed.connect(self._send)
        self.input.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(PAD, PAD, PAD, PAD + TAIL)
        layout.setSpacing(8)
        layout.addWidget(self.view)
        self.button_row = QWidget(self)
        self.button_layout = QHBoxLayout(self.button_row)
        self.button_layout.setContentsMargins(0, 0, 0, 0)
        self.button_layout.setSpacing(6)
        self.button_row.hide()
        layout.addWidget(self.button_row)
        layout.addWidget(self.input)
        self._on_choice: Callable[[str], None] | None = None

        self.target = ""  # full text received so far
        self.shown = 0  # how many characters are revealed
        self.final = False
        self.request_id = 0
        self.thinking = False
        self.dots = 0
        self.last_question = ""
        self.heard = ""  # last voice question (shown in the bubble)
        self.tail_down = True  # tail at the bottom (bubble above her head)
        self.tail_x = 0.5  # where along the width the tail sits (0..1)
        self._anchor = QPointF()

        self.typer = QTimer(self)
        self.typer.setInterval(30)
        self.typer.timeout.connect(self._type_tick)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._auto_hide)

    # --- public API
    def open_chat(self) -> None:
        """Show the bubble with the text box focused (click on her / chat hotkey)."""
        self.hide_timer.stop()
        self.input.show()
        if not self.target and not self.thinking:
            self._set_text(f"*{self.cat_name} looks up at you.*  Yes, {self.owner_name}?")
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        QTimer.singleShot(120, self._ensure_focus)

    def show_heard(self, text: str) -> None:
        """Voice input: show what she heard as your question, without stealing focus
        from the app you're working in."""
        self.heard = text
        self.input.show()
        self.target, self.shown, self.final = "", 0, False
        self._set_text("")
        if not self.isVisible():
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.show()
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

    def say(self, text: str) -> None:
        """A canned line (no AI): no text box, hides by itself."""
        if self.isVisible() and self.input.hasFocus():
            return  # don't interrupt you while you're typing
        self.input.hide()
        self.request_id = 0
        self.thinking = False
        self.target, self.shown, self.final = text, 0, True
        self.show()
        self.typer.start()

    def on_status(self, status: BrainStatus) -> None:
        if status.state == "thinking" and status.request_id >= self.request_id:
            self.request_id = status.request_id
            self.thinking = True
            self.target, self.shown, self.final = "", 0, False
            self.typer.start()

    def on_reply(self, reply: BrainReply) -> None:
        if reply.request_id and reply.request_id < self.request_id:
            return  # leftovers of a question you already replaced
        self.request_id = reply.request_id
        self.thinking = False
        self.target = reply.text
        self.final = reply.final
        if not self.isVisible():
            self.show()
        self.typer.start()

    def ask(self, text: str, choices: list[tuple[str, str]], on_choice) -> None:
        """Show `text` with buttons [(key, label)]; on_choice(key) once you pick one (or
        answer by voice: the app calls answer(key))."""
        self.clear_choices()
        self._on_choice = on_choice
        for key, label in choices:
            b = QPushButton(label, self.button_row)
            b.setFont(QFont("Segoe UI", 9))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { background: #f4efe6; border: 1px solid #d8cfc0; "
                "border-radius: 8px; padding: 3px 10px; color: #3a3440; }"
                "QPushButton:hover { background: #ebe2d3; }"
            )
            b.clicked.connect(lambda _=False, k=key: self.answer(k))
            self.button_layout.addWidget(b)
        self.button_layout.addStretch(1)
        self.button_row.show()
        self.input.hide()
        self.request_id = 0
        self.thinking = False
        self.target, self.shown, self.final = text, len(text), True
        self.hide_timer.stop()
        self._set_text(text)
        if not self.isVisible():
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.show()
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)

    @property
    def asking(self) -> bool:
        return self._on_choice is not None

    def answer(self, key: str) -> None:
        callback, self._on_choice = self._on_choice, None
        self.clear_choices()
        if callback is not None:
            callback(key)

    def clear_choices(self) -> None:
        while self.button_layout.count():
            item = self.button_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.button_row.hide()

    def close_chat(self) -> None:
        if self.asking:
            self.answer("dismissed")  # closed / kill switch = "no": risky things never run
        self.typer.stop()
        self.hide_timer.stop()
        was_visible = self.isVisible()
        self.hide()
        self.target, self.shown, self.thinking = "", 0, False
        self.heard = ""
        if was_visible:
            self.bus.publish(Event(Topic.CHAT_CLOSE))

    def follow(self, head: QPointF, screen: QRect) -> None:
        """Keep the tail pointing at `head` (logical px), staying on `screen`."""
        self._anchor = head
        w, h = self.width(), self.height()
        above = head.y() - h - 4 >= screen.top()
        self.tail_down = above
        y = head.y() - h - 4 if above else head.y() + 40
        x = head.x() - w / 2
        x = max(screen.left() + 4, min(x, screen.right() - w - 4))
        self.tail_x = min(max((head.x() - x) / max(w, 1), 0.12), 0.88)
        layout = self.layout()
        if above:
            layout.setContentsMargins(PAD, PAD, PAD, PAD + TAIL)
        else:
            layout.setContentsMargins(PAD, PAD + TAIL, PAD, PAD)
        pos = QPointF(x, y).toPoint()
        if pos != self.pos():
            self.move(pos)
        self.update()

    # --- internals
    def _ensure_focus(self) -> None:
        if self.isVisible() and not self.isActiveWindow():
            win.force_foreground(int(self.winId()))
            self.input.setFocus()

    def _send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.last_question = text
        self.heard = ""  # typed: no "You said" line
        self.input.clear()
        self.hide_timer.stop()
        self.bus.publish(Event(Topic.CHAT_ASK, text))

    def _type_tick(self) -> None:
        if self.thinking:  # animated "..." while she thinks
            self.dots = (self.dots + 1) % 12
            self._set_text("*thinking*" + "." * (1 + self.dots // 4))
            return
        step = max(1, round(self.cfg.chars_per_second * self.typer.interval() / 1000))
        if self.shown < len(self.target):
            # reveal whole words where possible, so Markdown doesn't flicker mid-word
            nxt = min(len(self.target), self.shown + step)
            space = self.target.find(" ", nxt)
            if 0 <= space - nxt < 8:
                nxt = space
            self.shown = nxt
            self._set_text(self.target[: self.shown])
        elif self.final:
            self.typer.stop()
            if self.asking:
                return  # waiting for a button: don't hide
            linger = self.cfg.linger_base + self.cfg.linger_per_char * len(self.target)
            self.hide_timer.start(int(linger * 1000))

    def _set_text(self, text: str) -> None:
        if self.heard:  # what she heard you say (voice input), above her answer
            text = f"*You: {self.heard}*\n\n{text}"
        self.view.setMarkdown(text)
        self._resize_to(text)
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())  # keep the newest words in view

    def _resize_to(self, text: str) -> None:
        inner_max = self.cfg.max_width - 2 * PAD
        doc = QTextDocument()
        doc.setDefaultFont(self.view.font())
        doc.setMarkdown(text)
        doc.setTextWidth(inner_max)
        width = min(inner_max, max(200, int(doc.idealWidth()) + 12))
        doc.setTextWidth(width)
        view_h = min(int(doc.size().height()) + 6, self.cfg.max_height)
        self.view.setFixedSize(width, max(view_h, 22))
        input_h = self.input.sizeHint().height() + 8 if self.input.isVisible() else 0
        if self.button_row.isVisible():
            input_h += self.button_row.sizeHint().height() + 8
        self.setFixedSize(width + 2 * PAD, self.view.height() + input_h + 2 * PAD + TAIL)
        if not self._anchor.isNull():
            self.follow(self._anchor, self.screen().geometry())

    def _auto_hide(self) -> None:
        if self.asking:
            return
        if self.underMouse() or (self.input.hasFocus() and self.input.text()):
            self.hide_timer.start(2000)  # you're reading or typing: wait
            return
        self.close_chat()

    # --- Qt events
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        if obj is self.input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.close_chat()
                return True
            if event.key() == Qt.Key.Key_Up and self.last_question:
                self.input.setText(self.last_question)
                return True
        return super().eventFilter(obj, event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hide_timer.stop()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.final and not self.typer.isActive() and self.isVisible():
            self.hide_timer.start(3000)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(
            1, 1 if self.tail_down else TAIL + 1, self.width() - 2, self.height() - TAIL - 2
        )
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS, RADIUS)
        tx = self.width() * self.tail_x
        tail = QPainterPath()
        if self.tail_down:
            tail.moveTo(tx - 9, body.bottom() - 1)
            tail.lineTo(tx + 2, self.height() - 1)
            tail.lineTo(tx + 9, body.bottom() - 1)
        else:
            tail.moveTo(tx - 9, body.top() + 1)
            tail.lineTo(tx + 2, 1)
            tail.lineTo(tx + 9, body.top() + 1)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(BORDER, 1.4))
        p.setBrush(BG)
        p.drawPath(path)
        p.end()

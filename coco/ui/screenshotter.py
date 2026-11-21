"""Takes screenshots: hides Coco, grabs, saves and copies."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QWidget

from coco.core.geometry import Rect
from coco.tools.registry import ToolCall, ToolResult
from coco.tools.screenshot import capture, cleanup_old, shot_name, shots_dir, to_b64_png
from coco.ui.region_select import RegionSelect

log = logging.getLogger(__name__)

SAID = {
    "en": "Got it! Saved in Pictures\\Coco and copied. *click*",
    "hinglish": "Ho gaya! Pictures\\Coco mein save kiya aur copy bhi kar diya. *click*",
}
SAID_HI = "हो गया! सेव कर दिया और कॉपी भी कर दिया।"
CANCELLED = {"en": "Okay, no screenshot.", "hinglish": "Theek hai, screenshot nahi liya."}


class Screenshotter:
    def __init__(
        self,
        cfg,  # core.config.ScreenshotConfig
        report: Callable[[ToolCall, ToolResult], None],
        monitor_rect: Callable[[], Rect],  # the monitor she's on (physical px)
        active_window: Callable[[], tuple[Rect, str] | None],  # (rect, title) or None
        windows: Callable[[], list[QWidget]],  # Coco's own windows (hidden for the shot)
        to_physical: Callable[[QWidget, object], Rect],  # (screen, logical QRect) -> Rect
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.cfg = cfg
        self.report = report
        self.monitor_rect = monitor_rect
        self.active_window = active_window
        self.windows = windows
        self.to_physical = to_physical
        self.now = now
        self._select: RegionSelect | None = None
        self.last_path = None

    # --- tools
    def take(self, call: ToolCall) -> None:
        target = call.arg("target", "screen").lower()
        if target in ("area", "region", "part"):
            self._pick_area(call)
            return
        rect = self._rect_for(target)
        self._without_coco(lambda: self._save(call, rect))

    def look(self, call: ToolCall) -> None:
        win = self.active_window()
        rect, where = win if win else (self.monitor_rect(), "the whole screen")
        self._without_coco(lambda: self._look(call, rect, where))

    def cancel(self) -> None:
        """Kill switch: close the area picker if it's open."""
        if self._select is not None:
            self._select.cancel()

    # --- helpers
    def _lang(self, call: ToolCall) -> str:
        return "hinglish" if call.lang == "hinglish" else "en"

    def _rect_for(self, target: str) -> Rect:
        if target in ("window", "app"):
            win = self.active_window()
            if win:
                return win[0]
        return self.monitor_rect()

    def _without_coco(self, grab: Callable[[], None]) -> None:
        hidden = []
        for w in self.windows():
            if w.isVisible():
                hidden.append((w, w.windowOpacity()))
                w.setWindowOpacity(0.0)

        def run() -> None:
            try:
                grab()
            finally:
                for w, opacity in hidden:
                    w.setWindowOpacity(opacity)

        QTimer.singleShot(self.cfg.hide_ms, run)

    def _pick_area(self, call: ToolCall) -> None:
        if self._select is not None:
            return

        def done(qrect, screen) -> None:
            self._select = None
            if qrect is None:
                lang = self._lang(call)
                self.report(call, ToolResult(ok=False, say=CANCELLED[lang], emotion="neutral"))
                return
            rect = self.to_physical(screen, qrect)
            self._without_coco(lambda: self._save(call, rect))

        self._select = RegionSelect(done)
        self._select.start()

    def _save(self, call: ToolCall, rect: Rect) -> None:
        lang = self._lang(call)
        folder = shots_dir(self.cfg.folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / shot_name(self.now())
        image = capture(rect)
        image.save(path, "PNG")
        QGuiApplication.clipboard().setImage(QImage(str(path)))
        self.last_path = path
        removed = cleanup_old(folder, self.cfg.keep_days)
        log.info("Screenshot %dx%d -> %s (%d old ones removed)", image.width, image.height,
                 path, removed)  # fmt: skip
        self.report(
            call,
            ToolResult(
                say=SAID[lang],
                speech_hi=SAID_HI if lang == "hinglish" else "",
                emotion="happy",
                data={"path": str(path)},
            ),  # fmt: skip
        )

    def _look(self, call: ToolCall, rect: Rect, where: str) -> None:
        image = capture(rect)
        b64 = to_b64_png(image, self.cfg.max_side_for_ai)
        log.info("Looking at %s (%dx%d, %d KB for the AI)", where, image.width, image.height,
                 len(b64) * 3 // 4 // 1024)  # fmt: skip
        self.report(
            call,
            ToolResult(
                image_b64=b64,
                facts=f"You looked at their screen ({where}). "
                "Explain what you see that answers their question.",
                emotion="curious",
            ),  # fmt: skip
        )

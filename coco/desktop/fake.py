"""A scriptable fake desktop for tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from coco.core.geometry import Point, Rect
from coco.desktop.base import MonitorInfo, TaskbarInfo, WindowInfo


@dataclass
class FakeDesktop:
    monitor_list: list[MonitorInfo] = field(
        default_factory=lambda: [
            MonitorInfo(
                device="\\\\.\\DISPLAY1",
                rect=Rect(0, 0, 1920, 1080),
                work=Rect(0, 0, 1920, 1032),  # 48 px taskbar at the bottom
                scale=1.0,
                primary=True,
            )
        ]
    )
    windows: list[WindowInfo] = field(default_factory=list)  # topmost first
    cursor: Point = Point(0, 0)
    idle: float = 0.0
    fullscreen: bool = False
    foreground: int | None = None

    def monitors(self) -> list[MonitorInfo]:
        return list(self.monitor_list)

    def top_level_windows(self, exclude_pids: frozenset[int] = frozenset()) -> list[WindowInfo]:
        return list(self.windows)

    def window_info(self, hwnd: int) -> WindowInfo | None:
        return next((w for w in self.windows if w.hwnd == hwnd), None)

    def foreground_window(self) -> int | None:
        return self.foreground

    def cursor_pos(self) -> Point:
        return self.cursor

    def user_idle_seconds(self) -> float:
        return self.idle

    def fullscreen_app_active(self) -> bool:
        return self.fullscreen

    def taskbar(self) -> TaskbarInfo | None:
        m = self.monitor_list[0]
        return TaskbarInfo(
            "bottom", Rect(m.rect.left, m.work.bottom, m.rect.right, m.rect.bottom), autohide=False
        )

    # --- helpers for tests
    def move_window(self, hwnd: int, rect: Rect) -> None:
        self.windows = [
            WindowInfo(w.hwnd, rect, w.title) if w.hwnd == hwnd else w for w in self.windows
        ]

    def close_window(self, hwnd: int) -> None:
        self.windows = [w for w in self.windows if w.hwnd != hwnd]

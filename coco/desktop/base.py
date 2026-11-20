"""The interface between Coco and the operating system."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from coco.core.geometry import Point, Rect


@dataclass(frozen=True)
class MonitorInfo:
    device: str  # e.g. "\\.\DISPLAY1" (matches QScreen.name())
    rect: Rect  # whole monitor
    work: Rect  # monitor minus the taskbar (equal to `rect` if the taskbar auto-hides)
    scale: float  # 1.0 at 100 %, 1.25 at 125 %, ...
    primary: bool = False


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    rect: Rect  # the visible frame (DWM extended frame bounds, no invisible borders)
    title: str = ""
    class_name: str = ""


@dataclass(frozen=True)
class TaskbarInfo:
    edge: str  # "bottom" | "top" | "left" | "right"
    rect: Rect
    autohide: bool


class DesktopAPI(Protocol):
    def monitors(self) -> list[MonitorInfo]:
        """All monitors. Never empty."""
        ...

    def top_level_windows(self, exclude_pids: frozenset[int] = frozenset()) -> list[WindowInfo]:
        """Visible, non-minimised, real app windows, in z-order (topmost first)."""
        ...

    def window_info(self, hwnd: int) -> WindowInfo | None:
        """Fresh info for one window, or None if it's gone / minimised / hidden / cloaked."""
        ...

    def foreground_window(self) -> int | None: ...

    def cursor_pos(self) -> Point: ...

    def user_idle_seconds(self) -> float:
        """Seconds since the last keyboard/mouse input anywhere."""
        ...

    def fullscreen_app_active(self) -> bool:
        """A game / video / presentation is fullscreen -> Coco should hide (polite mode)."""
        ...

    def taskbar(self) -> TaskbarInfo | None: ...

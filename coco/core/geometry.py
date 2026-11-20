"""Small geometry types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rect:
    """Axis-aligned rectangle. `right`/`bottom` are exclusive, like Win32 RECT."""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def center(self) -> Point:
        return Point((self.left + self.right) / 2, (self.top + self.bottom) / 2)

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def intersects(self, other: Rect) -> bool:
        return (
            self.left < other.right
            and other.left < self.right
            and self.top < other.bottom
            and other.top < self.bottom
        )

    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    @staticmethod
    def from_xywh(x: float, y: float, w: float, h: float) -> Rect:
        return Rect(x, y, x + w, y + h)


@dataclass(frozen=True, slots=True)
class Surface:
    """A horizontal line Coco can stand on: the taskbar top, a window's top edge, the bed.

    `id` identifies *what* the surface belongs to ("ground", "bed", or a window handle),
    so the engine can tell whether the thing she's standing on still exists next frame.
    """

    id: str | int
    y: float
    x1: float
    x2: float
    kind: str = "window"  # "ground" | "window" | "bed"

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    def covers_x(self, x: float, tol: float = 0.0) -> bool:
        return self.x1 - tol <= x <= self.x2 + tol


def subtract_intervals(
    start: float, end: float, holes: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Return the parts of [start, end] not covered by any (a, b) interval in `holes`.

    Used to cut a window's top edge into the pieces that aren't hidden behind other windows.
    """
    pieces = [(start, end)]
    for a, b in holes:
        next_pieces = []
        for s, e in pieces:
            if b <= s or a >= e:  # no overlap
                next_pieces.append((s, e))
                continue
            if a > s:
                next_pieces.append((s, a))
            if b < e:
                next_pieces.append((b, e))
        pieces = next_pieces
    return pieces

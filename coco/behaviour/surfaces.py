"""Turns window rectangles into surfaces Coco can stand on."""

from __future__ import annotations

from collections.abc import Iterable

from coco.behaviour.world import BedGeom
from coco.core.geometry import Rect, Surface, subtract_intervals
from coco.desktop.base import WindowInfo


def build_surfaces(
    windows: list[WindowInfo],
    monitor: Rect,
    work: Rect,
    *,
    min_width: float,
    headroom: float,
    bed: BedGeom | None = None,
) -> list[Surface]:
    """All surfaces on one monitor.

    min_width: ignore edge pieces narrower than this (she wouldn't fit).
    headroom:  ignore windows whose top is closer than this to the top of the monitor
               (e.g. maximised windows: no room for her to stand above them).
    """
    surfaces = [Surface("ground", work.bottom, work.left, work.right, "ground")]
    if bed is not None:
        surfaces.append(
            Surface(
                "bed",
                bed.spot.y,
                bed.spot.x - bed.half_width,
                bed.spot.x + bed.half_width,
                "bed",
            )
        )

    for i, win in enumerate(windows):
        r = win.rect
        y = r.top
        if y < monitor.top + headroom or y >= work.bottom - 4:
            continue  # no room above it, or it's down at/below the ground
        x1, x2 = max(r.left, work.left), min(r.right, work.right)
        if x2 - x1 < min_width:
            continue  # not on this monitor, or too narrow
        # Windows in front of this one that cover the line y = top edge.
        holes = [
            (o.rect.left, o.rect.right) for o in windows[:i] if o.rect.top <= y < o.rect.bottom
        ]
        for a, b in subtract_intervals(x1, x2, holes):
            if b - a >= min_width:
                surfaces.append(Surface(win.hwnd, y, a, b, "window"))
    return surfaces


def find_support(
    surfaces: Iterable[Surface], surface_id: str | int, x: float, y: float, tol: float
) -> Surface | None:
    """The surface Coco is standing on, if it still exists under her paws.

    Returns None when the window moved (more than `tol` px), closed, was minimised, or got
    covered by another window: then she should fall.
    """
    for s in surfaces:
        if s.id == surface_id and abs(s.y - y) <= tol and s.covers_x(x, tol):
            return s
    return None


def find_landing(surfaces: Iterable[Surface], x: float, y0: float, y1: float) -> Surface | None:
    """First surface crossed while falling from y0 down to y1 at horizontal position x.

    "Swept" check: we test the whole path travelled this frame, so a fast throw can't skip
    through a thin window edge between two frames. Only surfaces crossed from above count,
    so falling from inside a window's area never lands on that window's top edge.
    """
    best: Surface | None = None
    for s in surfaces:
        if y0 <= s.y <= y1 and s.covers_x(x) and (best is None or s.y < best.y):
            best = s
    return best


def segments_of(surfaces: Iterable[Surface], surface_id: str | int) -> list[Surface]:
    return [s for s in surfaces if s.id == surface_id]

"""What the engine knows about the desktop on a given frame."""

from __future__ import annotations

from dataclasses import dataclass, field

from coco.core.geometry import Point, Rect, Surface


@dataclass(frozen=True)
class BedGeom:
    rect: Rect  # where the bed image is drawn (physical px)
    spot: Point  # where Coco's paws rest when she's in it (the hammock centre)
    half_width: float  # how far left/right of `spot` she may stand in it
    outside: int = -1  # direction of the open floor: -1 = left (bed bottom-right), +1 = right


def place_bed(
    meta: dict,
    work: Rect,
    scale: float,
    cat_size_logical: float,
    corner: str = "bottom_right",
    margin_logical: float = 16,
    cat_frame_w: int = 256,
) -> BedGeom:
    """Where her bed goes: a bottom corner of the work area, standing on the ground.

    `meta` is assets/bed/bed.yaml (sizes in source px). The bed art is drawn at the same
    pixels-per-unit as her sprites (`scale_to_cat: 1.0`), so it's scaled by exactly the
    factor her frames get: display size / frame width (128 / 256 = 0.5 at size M).
    """
    src_w, src_h = meta["size"]
    k = cat_size_logical / cat_frame_w * float(meta.get("scale_to_cat", 1.0)) * scale
    width, height = src_w * k, src_h * k
    margin = margin_logical * scale
    if corner == "bottom_left":
        left, outside = work.left + margin, 1
    else:
        left, outside = work.right - margin - width, -1
    rect = Rect(left, work.bottom - height, left + width, work.bottom)
    spot_src = meta.get("bed", meta.get("spot", [src_w / 2, src_h / 2]))
    spot = Point(rect.left + spot_src[0] * k, rect.top + spot_src[1] * k)
    return BedGeom(rect, spot, float(meta.get("bed_half_width", 60)) * k, outside)


@dataclass
class WorldSnapshot:
    monitor: Rect  # Coco's monitor (physical px)
    work: Rect  # same monitor minus the taskbar
    virtual: Rect  # bounding box of all monitors (to know which edges are real edges)
    scale: float  # monitor scale factor (1.25 at 125 %)
    surfaces: list[Surface] = field(default_factory=list)  # ground, bed, window tops
    foreground: int | None = None  # hwnd of the active window
    cursor: Point = Point(-10_000, -10_000)
    user_idle: float = 0.0  # s since last keyboard/mouse input
    hour: float = 12.0  # local time of day, 0..24
    bed: BedGeom | None = None

    @property
    def ground(self) -> Surface:
        for s in self.surfaces:
            if s.kind == "ground":
                return s
        return Surface("ground", self.work.bottom, self.work.left, self.work.right, "ground")

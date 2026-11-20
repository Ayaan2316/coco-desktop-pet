r"""Generate simple placeholder sprites, the basket and a tray icon.

    .venv\Scripts\python tools_dev\make_placeholder_sprites.py

Frames are 256x256, facing left, paws at (128, 236), same as the real sprites.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
# Writes to sprites_placeholder/: the real Blender art lives in sprites/ and must never be
# overwritten by this script.
SPRITES_DIR = ROOT / "cats" / "coco" / "sprites_placeholder"
BASKET_DIR = ROOT / "assets" / "basket"
ICON_DIR = ROOT / "assets" / "icons"

SS = 4  # supersampling factor: draw at 4x, downscale = anti-aliased edges
FW = FH = 256  # frame size (source px)
AX, AY = 128, 236  # anchor: paw contact point
GROUND = AY

# Coco's palette (from her photo): white fluff, lavender-grey shading, pale blue eyes,
# faint grey-cream ear tips.
FUR = (252, 252, 255, 255)
FUR_FAR = (232, 233, 242, 255)  # far-side legs, a touch darker for depth
SHADE = (196, 200, 222)
OUTLINE = (104, 106, 132, 255)
INNER = (178, 180, 202, 255)
EAR_TIP = (200, 184, 164, 255)
EAR_INNER = (246, 196, 204, 255)
EYE = (150, 196, 234, 255)
EYE_RING = (88, 132, 182, 255)
PUPIL = (38, 48, 72, 255)
NOSE = (238, 150, 164, 255)
MOUTH = (140, 100, 112, 255)
MOUTH_IN = (176, 70, 90, 255)
TONGUE = (250, 150, 170, 255)
BLUSH = (255, 178, 192, 110)

OW = 3.4  # silhouette outline width (source px); ~1.7 px at the default 128 px size


def S(v: float) -> float:
    """Source px -> supersampled px."""
    return v * SS


# Shapes


@dataclass
class Shape:
    kind: str  # blob | ellipse | capsule | tail | poly
    data: tuple
    fill: tuple
    silhouette: bool = True  # contributes to the dark outer outline
    inner: bool = False  # gets its own light contour line (separates legs from body)


def blob_points(cx, cy, rx, ry, tufts=10, fluff=0.08, rot=0.0, phase=0.0, n=120):
    """Ellipse with a scalloped edge: |sin|^0.5 bumps = round fur tufts, sharp valleys."""
    pts = []
    c, s = math.cos(rot), math.sin(rot)
    for i in range(n):
        t = 2 * math.pi * i / n
        r = 1 + fluff * (abs(math.sin(tufts * t / 2 + phase)) ** 0.5 - 0.6)
        x, y = rx * r * math.cos(t), ry * r * math.sin(t)
        pts.append((cx + x * c - y * s, cy + x * s + y * c))
    return pts


def bezier(p0, p1, p2, t):
    u = 1 - t
    return (
        u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
        u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
    )


def _draw_shape(d: ImageDraw.ImageDraw, sh: Shape, color, grow: float) -> None:
    """Draw `sh` filled with `color`, enlarged by `grow` source px (for outline passes)."""
    g = grow
    if sh.kind in ("blob", "poly"):
        raw = blob_points(*sh.data) if sh.kind == "blob" else list(sh.data)
        pts = [(S(x), S(y)) for x, y in raw]
        d.polygon(pts, fill=color)
        if g:
            # Pillow draws polygon outlines *inside* the shape, so grow it with a centred
            # stroke instead: half of the line width lands outside the edge.
            d.line(pts + [pts[0]], fill=color, width=int(S(g) * 2), joint="curve")
            r = S(g)
            for x, y in pts[:: max(1, len(pts) // 40)] if sh.kind == "blob" else pts:
                d.ellipse([x - r, y - r, x + r, y + r], fill=color)
    elif sh.kind == "ellipse":
        cx, cy, rx, ry = sh.data
        d.ellipse([S(cx - rx - g), S(cy - ry - g), S(cx + rx + g), S(cy + ry + g)], fill=color)
    elif sh.kind == "capsule":
        (x1, y1), (x2, y2), w = sh.data
        r = w / 2 + g
        d.line([S(x1), S(y1), S(x2), S(y2)], fill=color, width=int(S(r * 2)))
        for x, y in ((x1, y1), (x2, y2)):
            d.ellipse([S(x - r), S(y - r), S(x + r), S(y + r)], fill=color)
    elif sh.kind == "tail":
        p0, p1, p2, r0, r1 = sh.data
        steps = 36
        for i in range(steps + 1):
            t = i / steps
            x, y = bezier(p0, p1, p2, t)
            r = r0 + (r1 - r0) * t + g
            d.ellipse([S(x - r), S(y - r), S(x + r), S(y + r)], fill=color)
        # fluffy plume at the tip
        tx, ty = p2
        pts = [(S(x), S(y)) for x, y in blob_points(tx, ty, r1 * 1.35 + g, r1 * 1.25 + g, 7, 0.12)]
        d.polygon(pts, fill=color)


def render(
    shapes: list[Shape],
    shades: list[tuple[float, float, float, float, int]],
    details: Callable[[ImageDraw.ImageDraw], None] | None,
    tilt: float = 0.0,
    pivot: tuple[float, float] = (AX, AY),
    offset: tuple[float, float] = (0, 0),
) -> Image.Image:
    size = (FW * SS, FH * SS)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 1) outer silhouette outline: every shape drawn slightly bigger in the outline colour
    for sh in shapes:
        if sh.silhouette:
            _draw_shape(d, sh, OUTLINE, OW)

    # 2) fills, back to front, with a light contour on shapes that need separating
    fill_mask = Image.new("L", size, 0)
    md = ImageDraw.Draw(fill_mask)
    for sh in shapes:
        if sh.inner:
            _draw_shape(d, sh, INNER, 1.4)
        _draw_shape(d, sh, sh.fill, 0)
        _draw_shape(md, sh, 255, 0)

    # 3) soft lavender shading (belly, under the chin), clipped to the fur
    if shades:
        shade = Image.new("L", size, 0)
        sd = ImageDraw.Draw(shade)
        for cx, cy, rx, ry, a in shades:
            sd.ellipse([S(cx - rx), S(cy - ry), S(cx + rx), S(cy + ry)], fill=a)
        shade = shade.filter(ImageFilter.GaussianBlur(S(7)))
        shade = Image.composite(shade, Image.new("L", size, 0), fill_mask)
        layer = Image.new("RGBA", size, SHADE + (0,))
        layer.putalpha(shade)
        img = Image.alpha_composite(img, layer)
        d = ImageDraw.Draw(img)

    # 4) face and other details on their own layer, then composited on top. (Drawing a
    #    semi-transparent colour like the blush or a whisker straight into the fur would
    #    replace the fur's alpha and punch see-through holes in her face.)
    if details:
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        details(ImageDraw.Draw(layer))
        img = Image.alpha_composite(img, layer)

    if tilt:
        img = img.rotate(
            -tilt, resample=Image.Resampling.BICUBIC, center=(S(pivot[0]), S(pivot[1]))
        )
    if offset != (0, 0):
        img = img.transform(
            size,
            Image.Transform.AFFINE,
            (1, 0, -S(offset[0]), 0, 1, -S(offset[1])),
            resample=Image.Resampling.BICUBIC,
        )
    return img.resize((FW, FH), Image.Resampling.LANCZOS)


# Pose + cat builder


@dataclass
class Pose:
    posture: str = "stand"  # stand | sit | loaf | curl | dangle
    dx: float = 0.0  # whole-cat offset
    dy: float = 0.0
    tilt: float = 0.0  # degrees, + = nose up
    pivot: tuple[float, float] | None = None
    crouch: float = 0.0  # 0..1 (stand postures)
    stretch: float = 0.0  # body elongation (run/pounce), may be negative
    breathe: float = 0.0  # -1..1
    legs: dict[str, tuple[float, float]] = field(default_factory=dict)  # name -> (dx, lift)
    head_dx: float = 0.0
    head_dy: float = 0.0
    eye_open: float = 1.0
    eye_style: str = "normal"  # normal | happy | sleep | wide
    look: float = 0.0  # pupils: -1 left .. 1 right
    mouth: str = "small"  # none | small | yawn | tongue
    mouth_open: float = 0.0
    ears: str = "up"  # up | back
    blush: bool = False
    tail_angle: float = 35.0  # stand: degrees above horizontal (pointing backwards)
    tail_lift: float = 0.0  # sit/loaf: how far the tail tip rises
    tail_swing: float = 0.0  # dangle: sideways swing of the tail tip
    paw_raise: float = 0.0  # sit: near front paw raised to the face (grooming)
    paw_wave: float = 0.0
    # emotions
    puff: float = 0.0  # 0..1 fur standing on end (angry)
    head_tilt: float = 0.0  # -1..1 head tilt (curious / thinking), done by shifting ears+eyes
    pupil_dy: float = 0.0  # -1 looking up .. 1 looking down
    tail_up: bool = False  # sit: tail held up behind her (happy) instead of wrapped


def head_parts(hx, hy, r, p: Pose, frontal=False):
    """Shapes (ears + head) and a detail painter (face) for a head centred at (hx, hy)."""
    shapes: list[Shape] = []
    fx = hx if frontal else hx - 0.24 * r  # face centre (3/4 view, facing left)
    fy = hy + 0.10 * r

    # Tiny ears with grey-cream tips, drawn before the head so the head covers their base.
    if frontal:
        ear_specs = [(fx - 0.52 * r, -1), (fx + 0.52 * r, 1)]
    else:
        ear_specs = [(fx - 0.50 * r, -1), (fx + 0.58 * r, 1)]
    for ex, side in ear_specs:
        base_y = hy - 0.62 * r + side * p.head_tilt * 0.12 * r
        bw, eh = 0.30 * r, 0.50 * r
        lean_k = {"back": 0.30, "droop": 0.42}.get(p.ears, 0.10)
        lean = side * lean_k * r
        drop = {"back": 0.22, "droop": 0.36}.get(p.ears, 0.0) * r
        tip = (ex + lean + side * 0.06 * r, base_y - eh + drop)
        a, b = (ex - bw, base_y + 0.12 * r), (ex + bw, base_y + 0.12 * r)
        shapes.append(Shape("poly", (a, tip, b), FUR, inner=True))
        # grey-cream tip: the top ~40% of the ear
        t = 0.42
        ta = (tip[0] + (a[0] - tip[0]) * t, tip[1] + (a[1] - tip[1]) * t)
        tb = (tip[0] + (b[0] - tip[0]) * t, tip[1] + (b[1] - tip[1]) * t)
        shapes.append(Shape("poly", (ta, tip, tb), EAR_TIP, silhouette=False))
        # pink inner ear
        ia = (a[0] + (tip[0] - a[0]) * 0.25 + bw * 0.35, a[1] - 0.10 * r)
        ib = (b[0] + (tip[0] - b[0]) * 0.25 - bw * 0.35, b[1] - 0.10 * r)
        itip = (tip[0] + (ex - tip[0]) * 0.25, tip[1] + 0.20 * eh)
        shapes.append(Shape("poly", (ia, itip, ib), EAR_INNER, silhouette=False))

    puff_r = 1 + 0.06 * p.puff
    head_blob = (hx, hy, 1.10 * r * puff_r, 0.96 * r * puff_r, 12, 0.07 + 0.10 * p.puff)
    shapes.append(Shape("blob", head_blob, FUR, inner=True))

    def face(d: ImageDraw.ImageDraw) -> None:
        er = 0.19 * r
        if frontal:
            eyes = [(fx - 0.36 * r, fy, 1.0), (fx + 0.36 * r, fy, 1.0)]
        else:
            eyes = [(fx - 0.36 * r, fy, 1.0), (fx + 0.32 * r, fy, 0.9)]

        if p.blush:
            for ex, ey, k in eyes:
                bx, by = ex - 0.02 * r, ey + 0.30 * r
                d.ellipse(
                    [S(bx - er * k), S(by - er * 0.5), S(bx + er * k), S(by + er * 0.5)], fill=BLUSH
                )

        eyes = [
            (ex, ey + (i * 2 - 1) * p.head_tilt * 0.08 * r, k) for i, (ex, ey, k) in enumerate(eyes)
        ]
        for ex, ey, k in eyes:
            rx, ry = er * k * 0.95, er * k * 1.08
            style = p.eye_style
            if style == "normal" and p.eye_open < 0.18:
                style = "sleep"
            if style in ("happy", "sleep"):
                # closed eyes: happy = ^ arc, sleeping = soft u arc
                box = [S(ex - rx), S(ey - ry * 0.7), S(ex + rx), S(ey + ry * 0.7)]
                start, end = (200, 340) if style == "happy" else (20, 160)
                d.arc(box, start, end, fill=PUPIL, width=int(S(1.8)))
                continue
            if style == "wide":
                rx, ry = rx * 1.08, ry * 1.12
            open_ry = ry * max(p.eye_open, 0.18)
            d.ellipse(
                [S(ex - rx - 0.9), S(ey - open_ry - 0.9), S(ex + rx + 0.9), S(ey + open_ry + 0.9)],
                fill=EYE_RING,
            )
            d.ellipse([S(ex - rx), S(ey - open_ry), S(ex + rx), S(ey + open_ry)], fill=EYE)
            pr = rx * (0.62 if style == "wide" else 0.52)
            px = ex + p.look * rx * 0.35
            pry = min(pr * 1.15, open_ry * 0.95)
            pcy = ey + p.pupil_dy * max(open_ry - pry, 0) * 0.9
            d.ellipse([S(px - pr), S(pcy - pry), S(px + pr), S(pcy + pry)], fill=PUPIL)
            if p.eye_open > 0.45:
                hr = rx * 0.30
                hx2, hy2 = px - pr * 0.35, ey - pry * 0.45
                d.ellipse([S(hx2 - hr), S(hy2 - hr), S(hx2 + hr), S(hy2 + hr)], fill=(255,) * 4)
                lr = hr * 0.5
                lx, ly = px + pr * 0.45, ey + pry * 0.4
                d.ellipse([S(lx - lr), S(ly - lr), S(lx + lr), S(ly + lr)], fill=(255,) * 4)

        # nose: small soft triangle
        nx, ny = fx - 0.01 * r, fy + 0.29 * r
        nw = 0.08 * r
        d.polygon(
            [
                (S(nx - nw), S(ny - nw * 0.6)),
                (S(nx + nw), S(ny - nw * 0.6)),
                (S(nx), S(ny + nw * 0.7)),
            ],
            fill=NOSE,
        )
        my = ny + nw * 0.7
        if p.mouth in ("open", "hiss") and p.mouth_open > 0.05:
            mh = 0.20 * r * p.mouth_open
            mw = 0.10 * r * (0.7 + 0.3 * p.mouth_open)
            d.ellipse([S(nx - mw), S(my), S(nx + mw), S(my + mh)], fill=MOUTH_IN)
            if p.mouth == "hiss":  # two tiny fangs
                for fxs in (-0.55, 0.55):
                    cx = nx + fxs * mw
                    fang = [
                        (S(cx - 1.6), S(my + 0.5)),
                        (S(cx + 1.6), S(my + 0.5)),
                        (S(cx), S(my + mh * 0.55)),
                    ]
                    d.polygon(fang, fill=(255, 255, 255, 255))
            elif p.mouth_open > 0.4:
                tongue = [S(nx - mw * 0.6), S(my + mh * 0.5), S(nx + mw * 0.6), S(my + mh)]
                d.ellipse(tongue, fill=TONGUE)
        elif p.mouth in ("yawn",) and p.mouth_open > 0.05:
            mh = 0.32 * r * p.mouth_open
            mw = 0.15 * r * (0.6 + 0.4 * p.mouth_open)
            d.ellipse([S(nx - mw), S(my), S(nx + mw), S(my + mh)], fill=MOUTH_IN)
            d.ellipse(
                [S(nx - mw * 0.7), S(my + mh * 0.45), S(nx + mw * 0.7), S(my + mh)], fill=TONGUE
            )
        elif p.mouth != "none":
            w = 0.07 * r
            d.arc(
                [S(nx - 2 * w), S(my - w), S(nx), S(my + w)], 0, 180, fill=MOUTH, width=int(S(1.3))
            )
            d.arc(
                [S(nx), S(my - w), S(nx + 2 * w), S(my + w)], 0, 180, fill=MOUTH, width=int(S(1.3))
            )
            if p.mouth == "tongue":
                d.ellipse(
                    [S(nx - w * 0.8), S(my + w * 0.5), S(nx + w * 0.8), S(my + w * 2.2)],
                    fill=TONGUE,
                )
        # a few faint whiskers
        for side in (-1, 1):
            for k in (-1, 0, 1):
                x0 = nx + side * 0.30 * r
                y0 = ny + 0.05 * r + k * 0.06 * r
                d.line(
                    [S(x0), S(y0), S(x0 + side * 0.45 * r), S(y0 + k * 0.10 * r - 0.03 * r)],
                    fill=(200, 202, 216, 200),
                    width=int(S(0.8)),
                )

    return shapes, face


def tail_shape(p0, p1, p2, r0=13.0, r1=10.0) -> Shape:
    return Shape("tail", (p0, p1, p2, r0, r1), FUR, inner=True)


def leg(root, foot, w, far=False) -> list[Shape]:
    """A stubby leg (capsule) with a rounded paw."""
    color = FUR_FAR if far else FUR
    paw = Shape(
        "ellipse", (foot[0] - 2, foot[1] - w * 0.32, w * 0.66, w * 0.42), color, inner=not far
    )
    return [Shape("capsule", (root, foot, w), color, inner=not far), paw]


def draw_cat(p: Pose) -> Image.Image:
    builder = {"stand": _stand, "sit": _sit, "loaf": _loaf, "curl": _curl, "dangle": _dangle}
    shapes, shades, details, pivot = builder[p.posture](p)
    return render(shapes, shades, details, tilt=p.tilt, pivot=p.pivot or pivot, offset=(p.dx, p.dy))


def _L(p: Pose, name: str) -> tuple[float, float]:
    return p.legs.get(name, (0.0, 0.0))


def _stand(p: Pose):
    bx = 140.0
    by = 186 + p.crouch * 16 + p.breathe
    rx = 58 + p.stretch * 12
    ry = 35 - p.crouch * 3 - p.stretch * 3
    hip_y = by + ry * 0.45

    roots = {
        "ff": (bx - 30 - p.stretch * 8, hip_y - 2),
        "bf": (bx + 44 + p.stretch * 8, hip_y - 4),
        "fn": (bx - 42 - p.stretch * 8, hip_y),
        "bn": (bx + 32 + p.stretch * 8, hip_y - 2),
    }

    def foot(name):
        ddx, lift = _L(p, name)
        rx_, _ = roots[name]
        return (rx_ + ddx, GROUND - 6 - lift)

    shapes: list[Shape] = []
    # far legs (behind the body)
    shapes += leg(roots["ff"], foot("ff"), 16, far=True)
    shapes += leg(roots["bf"], foot("bf"), 17, far=True)
    # tail
    a = math.radians(p.tail_angle)
    base = (bx + rx * 0.82, by - ry * 0.35)
    L = 66
    tip = (base[0] + L * math.cos(a) * 0.85, base[1] - L * math.sin(a))
    ctrl = (base[0] + L * 0.65, base[1] - L * 0.15 * math.sin(a))
    shapes.append(tail_shape(base, ctrl, tip, 14, 10))
    # body
    shapes.append(Shape("blob", (bx, by, rx, ry, 14, 0.07), FUR))
    # near legs
    shapes += leg(roots["fn"], foot("fn"), 19)
    shapes += leg(roots["bn"], foot("bn"), 20)
    # haunch tuft over the near back leg
    shapes.append(Shape("blob", (bx + 34 + p.stretch * 6, by + 4, 24, 22, 8, 0.10), FUR))
    # chest ruff (Persian mane)
    shapes.append(
        Shape("blob", (bx - 44 - p.stretch * 8, by - 4, 28, 28, 9, 0.12), FUR, inner=True)
    )
    hx = bx - 62 - p.stretch * 10 + p.head_dx
    hy = by - 42 + p.crouch * 8 + p.head_dy
    hs, face = head_parts(hx, hy, 46, p)
    shapes += hs
    shades = [(bx + 4, by + ry * 0.75, rx * 0.8, ry * 0.4, 140), (hx + 4, hy + 46, 26, 10, 90)]
    return shapes, shades, face, (bx + 34, GROUND)


def _sit(p: Pose):
    bx, by = 150.0, 200.0
    br = p.breathe
    shapes: list[Shape] = []
    tr = 1 + 0.7 * p.puff  # puffed-up tail
    if p.tail_up:  # happy / angry: tail raised behind her
        base = (bx + 40, by + 6)
        ctrl = (bx + 74, by - 20)
        tip = (bx + 62 + p.tail_lift * 10, by - 74)
    else:  # tail wraps along the ground towards the front paws
        base = (bx + 44, by + 24)
        tip = (bx - 66 + p.tail_lift * 8, GROUND - 8 - p.tail_lift * 26)
        ctrl = (bx + 30, GROUND + 8 - p.tail_lift * 6)
    shapes.append(tail_shape(base, ctrl, tip, 13 * tr, 10 * tr))
    # haunch
    fl = 0.08 + 0.10 * p.puff
    pf = 4 * p.puff
    shapes.append(Shape("blob", (bx, by + 6, 50 + pf, 32 + br + pf, 12, fl), FUR))
    # far front leg
    shapes += leg((bx - 26, by - 6), (bx - 25, GROUND - 6), 16, far=True)
    # chest
    chest = (bx - 34, by - 26 - br, 37 + pf, 44 + br * 1.5 + pf, 12, fl)
    shapes.append(Shape("blob", chest, FUR))
    # near front leg (or raised to the face while grooming)
    hx = bx - 38 + p.head_dx
    hy = by - 96 + p.head_dy - br
    if p.paw_raise > 0:
        k = p.paw_raise
        target = (hx - 30, hy + 34 + p.paw_wave)
        foot = ((bx - 46) * (1 - k) + target[0] * k, (GROUND - 6) * (1 - k) + target[1] * k)
        shapes += leg((bx - 44, by - 14), foot, 18)
    else:
        shapes += leg((bx - 46, by - 10), (bx - 48, GROUND - 6), 19)
    # chest ruff
    shapes.append(Shape("blob", (bx - 40, by - 50 - br, 30, 24, 9, 0.12), FUR, inner=True))
    hs, face = head_parts(hx, hy, 48, p)
    shapes += hs
    if p.paw_raise > 0:  # paw drawn over the face
        k = p.paw_raise
        shapes.append(Shape("ellipse", (hx - 30, hy + 34 + p.paw_wave, 11, 9), FUR, inner=True))
    shades = [
        (bx + 6, by + 30, 46, 14, 150),
        (bx - 34, by - 10, 22, 20, 70),
        (hx + 6, hy + 50, 26, 10, 90),
    ]
    return shapes, shades, face, (bx, GROUND)


def _loaf(p: Pose):
    bx, by = 136.0, 210.0
    br = p.breathe
    shapes: list[Shape] = []
    shapes.append(Shape("blob", (bx, by - br, 74, 30 + br, 14, 0.07), FUR))
    hx, hy = 86 + p.head_dx, 164 + p.head_dy - br
    shapes.append(Shape("blob", (bx - 40, by - 16, 30, 24, 9, 0.12), FUR, inner=True))
    # tiny front paws peeking out
    shapes.append(Shape("ellipse", (78, GROUND - 7, 11, 7), FUR, inner=True))
    shapes.append(Shape("ellipse", (98, GROUND - 6, 11, 7), FUR, inner=True))
    # tail tucked along the side
    tip_lift = p.tail_lift * 18
    shapes.append(
        tail_shape(
            (bx + 66, by + 10), (bx + 40, GROUND + 4), (bx - 20, GROUND - 6 - tip_lift), 12, 9
        )
    )
    hs, face = head_parts(hx, hy, 46, p)
    shapes += hs
    shades = [(bx + 8, by + 22, 64, 10, 150), (hx + 6, hy + 46, 26, 10, 90)]
    return shapes, shades, face, (bx, GROUND)


def _curl(p: Pose):
    bx, by = 134.0, 212.0
    br = p.breathe
    shapes: list[Shape] = []
    shapes.append(Shape("blob", (bx, by - br * 1.5, 72, 28 + br * 2, 15, 0.07), FUR))
    hx, hy = 84 + p.head_dx, 206 + p.head_dy - br
    hs, face = head_parts(hx, hy, 38, p)
    shapes += hs
    # tail wraps around the front, over her chin
    shapes.append(tail_shape((bx + 68, by + 8), (bx + 10, GROUND + 14), (58, GROUND - 14), 12, 9))
    shades = [(bx + 10, by + 20, 64, 10, 160), (bx + 20, by - 8, 40, 14, 50)]
    return shapes, shades, face, (bx, GROUND)


def _dangle(p: Pose):
    hx, hy = 128.0, 84.0
    shapes: list[Shape] = []
    sw = p.tail_swing
    shapes.append(tail_shape((128, 192), (128 + sw * 0.3, 222), (128 + sw, 248), 12, 9))
    k = p.legs.get("kick", (0, 0))[0]
    shapes += leg((110, 186), (106 - k, 226), 17, far=True)
    shapes += leg((146, 186), (150 + k, 226), 17, far=True)
    shapes.append(Shape("blob", (128, 152, 40, 50, 12, 0.08), FUR))
    shapes += leg((106, 128), (100 + k, 174), 17)
    shapes += leg((150, 128), (156 - k, 174), 17)
    shapes.append(Shape("blob", (128, 116, 32, 20, 9, 0.12), FUR, inner=True))
    hs, face = head_parts(hx, hy, 44, p, frontal=True)
    shapes += hs
    shades = [(128, 186, 32, 14, 140), (128, 128, 24, 10, 80)]
    return shapes, shades, face, (128, 46)


# Animations


def wave(i: int, n: int, phase: float = 0.0) -> float:
    return math.sin(2 * math.pi * i / n + phase)


def anim_sit(n=8):
    return [
        Pose(
            "sit",
            breathe=1.4 * wave(i, n),
            tail_lift=0.15 + 0.1 * wave(i, n, 1),
            ears="back" if i == 5 else "up",
        )
        for i in range(n)
    ]


def anim_blink():
    return [Pose("sit", eye_open=v) for v in (1.0, 0.35, 0.0, 0.55)]


def anim_tail_swish(n=8):
    return [Pose("sit", tail_lift=0.5 + 0.5 * wave(i, n, -math.pi / 2)) for i in range(n)]


def anim_loaf(n=6):
    return [
        Pose("loaf", breathe=1.2 * wave(i, n), eye_open=0.55 if i < 3 else 0.5) for i in range(n)
    ]


def anim_groom(n=10):
    frames = []
    for i in range(n):
        w = wave(i, n)
        frames.append(
            Pose(
                "sit",
                paw_raise=1.0,
                paw_wave=6 * w,
                head_dx=2,
                head_dy=4,
                eye_style="happy",
                mouth="tongue" if w > 0.2 else "small",
            )
        )
    return frames


def anim_yawn():
    opens = [0, 0.3, 0.7, 1, 1, 0.7, 0.3, 0]
    return [
        Pose(
            "sit",
            mouth="yawn",
            mouth_open=o,
            eye_style="happy" if o > 0.5 else "normal",
            eye_open=1 - o * 0.6,
            head_dy=-4 * o,
            ears="back" if o > 0.8 else "up",
        )
        for o in opens
    ]


def anim_look_around():
    looks = [0, -0.6, -1, -1, -0.3, 0.4, 1, 1, 0.5, 0]
    return [
        Pose(
            "sit",
            look=v,
            head_dx=4 * v,
            head_dy=-1 if abs(v) > 0.8 else 0,
            eye_open=0.25 if i == 4 else 1.0,
        )
        for i, v in enumerate(looks)
    ]


def anim_purr(n=6):
    return [
        Pose(
            "sit",
            eye_style="happy",
            blush=True,
            breathe=2 * wave(i, n),
            dx=0.6 if i % 2 else -0.6,
            tail_lift=0.3 + 0.3 * wave(i, n),
        )
        for i in range(n)
    ]


def anim_sleep(n=6):
    return [
        Pose("curl", breathe=1.6 * wave(i, n), eye_style="sleep", mouth="none") for i in range(n)
    ]


def anim_wake_up():
    return [
        Pose("curl", eye_style="sleep", mouth="none"),
        Pose("curl", eye_style="sleep", mouth="none", head_dy=-6),
        Pose("loaf", eye_open=0.25),
        Pose("loaf", eye_open=0.6),
        Pose("loaf", eye_open=1.0, look=-0.5),
        Pose("sit", eye_open=1.0, look=-0.2),
        Pose("sit", eye_open=0.3),
        Pose("sit", eye_open=1.0),
    ]


def anim_walk(n=8):
    frames = []
    for i in range(n):
        ph = 2 * math.pi * i / n

        def lg(phase):
            return (-13 * math.sin(phase), max(0.0, math.cos(phase)) * 8)

        frames.append(
            Pose(
                "stand",
                breathe=-1.5 * abs(math.sin(2 * ph)),
                legs={
                    "fn": lg(ph),
                    "bf": lg(ph + 0.3),
                    "ff": lg(ph + math.pi),
                    "bn": lg(ph + math.pi + 0.3),
                },
                head_dy=-1.0 * abs(math.sin(2 * ph)),
                tail_angle=40 + 7 * math.sin(ph),
            )
        )
    return frames


def anim_run():
    spec = [  # (front dx, front lift, back dx, back lift, stretch, tilt)
        (-26, 6, 24, 4, 1.0, 0),
        (-10, 0, 12, 10, 0.5, -4),
        (10, 6, -14, 6, -0.3, 0),
        (8, 14, -10, 14, -0.2, 3),
        (-20, 14, 22, 0, 0.6, 6),
        (-28, 9, 26, 10, 1.0, 2),
    ]
    return [
        Pose(
            "stand",
            legs={"fn": (f, fl), "ff": (f + 6, fl + 2), "bn": (b, bl), "bf": (b + 6, bl + 2)},
            stretch=s,
            tilt=t,
            tail_angle=20 + 6 * t,
            ears="back",
        )
        for f, fl, b, bl, s, t in spec
    ]


def anim_jump():
    return [
        Pose("stand"),
        Pose("stand", crouch=0.6, tail_angle=25),
        Pose("stand", crouch=1.0, tail_angle=15, ears="back"),
        Pose(
            "stand",
            tilt=24,
            stretch=0.8,
            legs={"fn": (-14, 30), "ff": (-8, 28), "bn": (22, 0), "bf": (26, 0)},
            tail_angle=5,
        ),
        Pose(
            "stand",
            tilt=18,
            stretch=0.4,
            legs={"fn": (-6, 34), "ff": (-2, 32), "bn": (16, 22), "bf": (20, 20)},
            tail_angle=15,
        ),
        Pose(
            "stand",
            tilt=10,
            legs={"fn": (-10, 26), "ff": (-6, 24), "bn": (12, 22), "bf": (16, 20)},
            tail_angle=25,
        ),
    ]


def anim_fall():
    spec = [
        (-14, 8, 12, 10, 3, 78),
        (-18, 12, 16, 8, -2, 70),
        (-12, 10, 10, 12, 2, 84),
        (-16, 6, 14, 10, -3, 74),
    ]
    return [
        Pose(
            "stand",
            legs={"fn": (f, fl), "ff": (f + 8, fl + 4), "bn": (b, bl), "bf": (b + 8, bl + 4)},
            tilt=t,
            tail_angle=ta,
            eye_style="wide",
            mouth="small",
        )
        for f, fl, b, bl, t, ta in spec
    ]


def anim_land():
    return [
        Pose(
            "stand",
            legs={"fn": (-10, 0), "ff": (-4, 0), "bn": (10, 0), "bf": (14, 0)},
            crouch=0.3,
            tilt=-5,
            eye_style="wide",
        ),
        Pose("stand", crouch=1.0, tail_angle=20),
        Pose("stand", crouch=0.5, tail_angle=30),
        Pose("stand", crouch=0.0),
    ]


def anim_stretch():
    frames = []
    seq = [0, 0.5, 1, 1, 1, 1, 0.6, 0.2, -0.4, 0]
    for i, k in enumerate(seq):
        if k >= 0:
            frames.append(
                Pose(
                    "stand",
                    tilt=-11 * k,
                    pivot=(176, GROUND),
                    legs={"fn": (-24 * k, 18 * k), "ff": (-18 * k, 16 * k)},
                    eye_style="happy" if k > 0.8 else "normal",
                    mouth="yawn" if 2 <= i <= 4 else "small",
                    mouth_open=0.7 if i == 3 else 0.4,
                    tail_angle=55 + 20 * k,
                    ears="back" if k > 0.8 else "up",
                )
            )
        else:  # back legs stretched out behind
            frames.append(Pose("stand", legs={"bn": (24, 6), "bf": (28, 4)}, tilt=4, tail_angle=30))
    return frames


def anim_stalk(n=6):
    frames = []
    for i in range(n):
        w = wave(i, n)
        frames.append(
            Pose(
                "stand",
                crouch=1.0,
                ears="back",
                head_dx=-10,
                head_dy=12,
                eye_style="wide",
                look=-0.6,
                mouth="none",
                legs={
                    "fn": (-4 * w, max(0, w) * 4),
                    "ff": (4 * w, max(0, -w) * 4),
                    "bn": (3 * w, 0),
                    "bf": (-3 * w, 0),
                },
                dx=1.5 * w,
                tail_angle=-4 + 8 * max(0, wave(i, n, 1)),
            )
        )
    return frames


def anim_pounce():
    return [
        Pose(
            "stand",
            tilt=18,
            legs={"fn": (-20, 26), "ff": (-14, 24), "bn": (22, 0), "bf": (26, 0)},
            stretch=0.6,
            eye_style="wide",
            tail_angle=0,
        ),
        Pose(
            "stand",
            tilt=12,
            stretch=1.2,
            legs={"fn": (-30, 30), "ff": (-24, 28), "bn": (30, 18), "bf": (34, 16)},
            eye_style="wide",
            tail_angle=10,
        ),
        Pose(
            "stand",
            tilt=0,
            stretch=0.8,
            legs={"fn": (-24, 16), "ff": (-18, 14), "bn": (24, 20), "bf": (28, 18)},
            eye_style="wide",
            tail_angle=30,
        ),
        Pose(
            "stand",
            tilt=-12,
            legs={"fn": (-16, 4), "ff": (-10, 4), "bn": (18, 20), "bf": (22, 18)},
            tail_angle=50,
        ),
    ]


def anim_peek():
    hdx = [14, 6, -4, -8, -8, -8, -2, 10]
    looks = [0, 0, -0.5, -1, 0.2, 1, 0, 0]
    return [
        Pose(
            "stand",
            crouch=0.5,
            head_dx=h,
            head_dy=4,
            look=v,
            eye_open=0.1 if i == 4 else 1.0,
            ears="up" if i else "back",
        )
        for i, (h, v) in enumerate(zip(hdx, looks, strict=True))
    ]


def anim_dangle(n=6):
    frames = []
    for i in range(n):
        w = wave(i, n)
        frames.append(
            Pose(
                "dangle",
                tilt=7 * w,
                pivot=(128, 46),
                tail_swing=-14 * w,
                legs={"kick": (3 * wave(i, n, 1), 0)},
                eye_style="wide",
                mouth="small",
            )
        )
    return frames


# --- talking + emotions


def anim_talk():
    pairs = ((0.2, 0), (0.75, 0.5), (0.35, 0.8), (0.85, 0.3))
    return [Pose("sit", mouth="open", mouth_open=o, breathe=b) for o, b in pairs]


def anim_think(n=6):
    return [
        Pose(
            "sit",
            head_tilt=0.8,
            look=0.4,
            pupil_dy=-0.8,
            paw_raise=0.55,
            mouth="none",
            head_dx=1.5 * wave(i, n),
            eye_open=0.25 if i == 3 else 1.0,
        )
        for i in range(n)
    ]


def anim_happy():
    bounce = [0, -3, -7, -4, -1, 0]
    return [
        Pose(
            "sit",
            eye_style="happy",
            tail_up=True,
            blush=True,
            dy=b,
            tail_lift=0.6 * (i % 2),
            breathe=-b / 3,
        )
        for i, b in enumerate(bounce)
    ]


def anim_sad(n=6):
    return [
        Pose(
            "sit",
            ears="droop",
            eye_open=0.6,
            pupil_dy=0.7,
            head_dy=7,
            mouth="small",
            breathe=0.8 * wave(i, n),
            look=-0.2,
        )
        for i in range(n)
    ]


def anim_angry(n=6):
    return [
        Pose(
            "sit",
            puff=0.8 + 0.2 * abs(wave(i, n)),
            ears="back",
            eye_open=0.55,
            mouth="hiss",
            mouth_open=0.55 + 0.25 * abs(wave(i, n)),
            tail_up=True,
            dx=0.8 if i % 2 else -0.8,
        )
        for i in range(n)
    ]


def anim_surprised():
    hops = [0, -10, -14, -6, 0]
    return [
        Pose(
            "sit",
            dy=h,
            eye_style="wide" if i else "normal",
            mouth="open",
            mouth_open=0.0 if i == 0 else 0.45,
            tail_up=i in (1, 2, 3),
        )
        for i, h in enumerate(hops)
    ]


def anim_curious(n=8):
    return [
        Pose(
            "sit",
            head_tilt=wave(i, n),
            look=0.4 * wave(i, n, 1),
            eye_style="wide",
            mouth="none",
            head_dx=2 * wave(i, n),
        )
        for i in range(n)
    ]


def anim_sleepy(n=6):
    return [
        Pose(
            "sit",
            eye_open=0.32 if i % 3 else 0.2,
            head_dy=4,
            dx=2 * wave(i, n),
            ears="back" if i in (2, 3) else "up",
            mouth="small",
        )
        for i in range(n)
    ]


def anim_love():
    lids = [1.0, 0.7, 0.25, 0.0, 0.0, 0.25, 0.7, 1.0]  # a slow blink = "I love you" in cat
    return [Pose("sit", eye_open=v, blush=True, tail_lift=0.4, look=-0.3) for v in lids]


ANIMATIONS: dict[str, Callable[[], list[Pose]]] = {
    "sit": anim_sit,
    "blink": anim_blink,
    "tail_swish": anim_tail_swish,
    "loaf": anim_loaf,
    "groom": anim_groom,
    "yawn": anim_yawn,
    "stretch": anim_stretch,
    "look_around": anim_look_around,
    "purr": anim_purr,
    "sleep": anim_sleep,
    "wake_up": anim_wake_up,
    "walk": anim_walk,
    "run": anim_run,
    "jump": anim_jump,
    "fall": anim_fall,
    "land": anim_land,
    "peek": anim_peek,
    "stalk": anim_stalk,
    "pounce": anim_pounce,
    "talk": anim_talk,
    "think": anim_think,
    "happy": anim_happy,
    "sad": anim_sad,
    "angry": anim_angry,
    "surprised": anim_surprised,
    "curious": anim_curious,
    "sleepy": anim_sleepy,
    "love": anim_love,
    "dangle": anim_dangle,
}


# Basket + icon

BW, BH = 256, 176  # basket source size
WEAVE_A = (222, 186, 128, 255)
WEAVE_B = (236, 206, 152, 255)
WEAVE_DARK = (168, 128, 80, 255)
WEAVE_LINE = (128, 92, 52, 255)
INSIDE = (110, 78, 48, 255)
CUSHION = (196, 214, 236, 255)


def _woven_band(d, box_fn, rows, cols, y0, y1, x_at):
    """Fill rows of staggered rounded 'reeds' between y0..y1; x_at(y) -> (left, right)."""
    row_h = (y1 - y0) / rows
    for r in range(rows):
        top = y0 + r * row_h
        left, right = x_at(top + row_h / 2)
        width = right - left
        cw = width / cols
        offset = (cw / 2) if r % 2 else 0
        for c in range(-1, cols + 1):
            x = left + c * cw + offset
            xa, xb = max(x + 1, left), min(x + cw - 1, right)
            if xb - xa < 2:
                continue
            fill = WEAVE_A if (r + c) % 2 else WEAVE_B
            d.rounded_rectangle(
                [S(xa), S(top + 1), S(xb), S(top + row_h - 1)],
                radius=S(row_h * 0.45),
                fill=fill,
                outline=WEAVE_DARK,
                width=int(S(1.1)),
            )


def make_basket() -> tuple[Image.Image, Image.Image, dict]:
    size = (BW * SS, BH * SS)
    cx, rim_y, rim_rx, rim_ry = 128, 70, 116, 34
    bottom = 168

    def side_x(y):
        t = (y - rim_y) / (bottom - rim_y)
        inset = 10 * t
        return (cx - rim_rx + inset, cx + rim_rx - inset)

    # --- back layer: rim ring + dark inside + cushion
    back = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(back)
    d.ellipse(
        [S(cx - rim_rx - 2), S(rim_y - rim_ry - 2), S(cx + rim_rx + 2), S(rim_y + rim_ry + 2)],
        fill=WEAVE_LINE,
    )
    d.ellipse([S(cx - rim_rx), S(rim_y - rim_ry), S(cx + rim_rx), S(rim_y + rim_ry)], fill=WEAVE_A)
    # woven texture on the back half of the rim
    for i in range(28):
        a = math.pi + math.pi * i / 27
        x, y = cx + (rim_rx - 7) * math.cos(a), rim_y + (rim_ry - 6) * math.sin(a)
        d.ellipse(
            [S(x - 7), S(y - 4), S(x + 7), S(y + 4)],
            fill=WEAVE_B if i % 2 else WEAVE_A,
            outline=WEAVE_DARK,
            width=int(S(1)),
        )
    irx, iry = rim_rx - 14, rim_ry - 10
    d.ellipse([S(cx - irx), S(rim_y - iry), S(cx + irx), S(rim_y + iry)], fill=INSIDE)
    # the inside extends down behind the front wall
    d.rectangle([S(cx - irx), S(rim_y), S(cx + irx), S(bottom - 14)], fill=INSIDE)
    # soft blue cushion
    d.ellipse([S(cx - irx + 8), S(rim_y + 4), S(cx + irx - 8), S(rim_y + 50)], fill=CUSHION)
    d.ellipse(
        [S(cx - irx + 20), S(rim_y + 8), S(cx + irx - 20), S(rim_y + 34)], fill=(214, 228, 244, 255)
    )

    # --- front layer: woven front wall + front half of the rim
    front = Image.new("RGBA", size, (0, 0, 0, 0))
    mask = Image.new("L", size, 0)
    md = ImageDraw.Draw(mask)
    poly = []
    for i in range(41):  # front half of the rim ellipse, left -> right
        a = math.pi - math.pi * i / 40
        poly.append((cx + rim_rx * math.cos(a), rim_y + rim_ry * math.sin(a)))
    l_bot, r_bot = side_x(bottom)
    poly += [(r_bot, bottom - 10), (r_bot - 10, bottom), (l_bot + 10, bottom), (l_bot, bottom - 10)]
    md.polygon([(S(x), S(y)) for x, y in poly], fill=255)

    weave = Image.new("RGBA", size, (0, 0, 0, 0))
    wd = ImageDraw.Draw(weave)
    _woven_band(wd, None, rows=7, cols=12, y0=rim_y - 4, y1=bottom, x_at=side_x)
    front.paste(weave, (0, 0), mask)
    fd = ImageDraw.Draw(front)
    # outline of the wall
    fd.line(
        [(S(x), S(y)) for x, y in poly] + [(S(poly[0][0]), S(poly[0][1]))],
        fill=WEAVE_LINE,
        width=int(S(2)),
    )
    # thick rim band (front half)
    for i in range(30):
        a = math.pi - math.pi * i / 29
        x, y = cx + (rim_rx - 4) * math.cos(a), rim_y + (rim_ry - 2) * math.sin(a)
        fd.ellipse(
            [S(x - 9), S(y - 6), S(x + 9), S(y + 6)],
            fill=WEAVE_B if i % 2 else WEAVE_A,
            outline=WEAVE_LINE,
            width=int(S(1.2)),
        )
    # gentle shading towards the bottom
    shade = Image.new("L", size, 0)
    sd = ImageDraw.Draw(shade)
    sd.rectangle([0, S(bottom - 34), S(BW), S(bottom)], fill=70)
    shade = shade.filter(ImageFilter.GaussianBlur(S(10)))
    shade = Image.composite(shade, Image.new("L", size, 0), mask)
    dark = Image.new("RGBA", size, (90, 60, 30, 0))
    dark.putalpha(shade)
    front = Image.alpha_composite(front, dark)

    meta = {
        "back": "basket_back.png",
        "front": "basket_front.png",
        "size": [BW, BH],
        "scale_to_cat": 1.0,  # displayed basket width = this x cat frame display width
        "bed": [128, 120],  # where her paws rest inside (source px)
        "bed_half_width": 64,  # how far left/right of `bed` she can stand inside
        "notes": "Back is drawn behind Coco, front in front of her, so she sits INSIDE.",
    }
    down = Image.Resampling.LANCZOS
    return back.resize((BW, BH), down), front.resize((BW, BH), down), meta


BUBBLE_DIR = ROOT / "assets" / "bubbles"


def make_mood_icons() -> dict[str, Image.Image]:
    """Small icons shown above her head: heart, zzz, ! and ?  (64x64, white outline)."""
    size = 64 * SS

    def outlined(draw_fn, color):
        # draw the shape fat in white (the outline), then normal-sized in colour on top
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        draw_fn(d, (255, 255, 255, 255), S(3.2))
        draw_fn(d, color, 0)
        return img.resize((64, 64), Image.Resampling.LANCZOS)

    def heart(d, c, g):
        r = S(13)
        cx, cy = S(32), S(28)
        d.ellipse([cx - 2 * r - g + S(1), cy - r - g, cx + g + S(1), cy + r + g], fill=c)
        d.ellipse([cx - g - S(1), cy - r - g, cx + 2 * r + g - S(1), cy + r + g], fill=c)
        tri = [
            (cx - 2 * r - g + S(2), cy + S(4)),
            (cx + 2 * r + g - S(2), cy + S(4)),
            (cx, S(56) + g),
        ]
        d.polygon(tri, fill=c)

    def zzz(d, c, g):
        for x, y, h in ((2, 42, 15), (24, 25, 12), (44, 8, 10)):  # spaced: outlines must not merge
            pts = [(S(x), S(y)), (S(x + h), S(y)), (S(x), S(y + h)), (S(x + h), S(y + h))]
            d.line(pts, fill=c, width=int(S(3.6) + 2 * g), joint="curve")

    def exclaim(d, c, g):
        d.rounded_rectangle([S(26) - g, S(6) - g, S(38) + g, S(40) + g], radius=S(6), fill=c)
        d.ellipse([S(25) - g, S(45) - g, S(39) + g, S(59) + g], fill=c)

    def question(d, c, g):
        w = int(S(7) + 2 * g)
        d.arc([S(16) - g, S(5) - g, S(48) + g, S(35) + g], 200, 430, fill=c, width=w)
        d.line([(S(36), S(31)), (S(32), S(38)), (S(32), S(44))], fill=c, width=w, joint="curve")
        d.ellipse([S(26) - g, S(48) - g, S(38) + g, S(60) + g], fill=c)

    def ear(d, c, g):
        # a wide cat ear with a pink inside, plus two "sound" arcs: she's listening
        d.polygon([(S(4) - g, S(56) + g), (S(19), S(12) - g * 1.5), (S(36) + g, S(56) + g)], fill=c)
        if g == 0:  # inner ear only on the colour pass, not the white outline
            d.polygon([(S(11), S(52)), (S(19), S(26)), (S(28), S(52))], fill=(250, 190, 200, 255))
        w = int(S(4.5) + 2 * g)
        d.arc([S(33) - g, S(24) - g, S(49) + g, S(48) + g], -55, 55, fill=c, width=w)
        d.arc([S(37) - g, S(13) - g, S(62) + g, S(58) + g], -55, 55, fill=c, width=w)

    return {
        "heart": outlined(heart, (247, 110, 140, 255)),
        "zzz": outlined(zzz, (120, 140, 200, 255)),
        "exclaim": outlined(exclaim, (245, 160, 40, 255)),
        "question": outlined(question, (90, 150, 230, 255)),
        "ear": outlined(ear, (60, 165, 150, 255)),  # the microphone is open
    }


def make_icon() -> Image.Image:
    p = Pose("sit")
    hs, face = head_parts(128, 140, 96, p)
    img = render(hs, [], face)
    return img.crop((20, 30, 236, 246)).resize((64, 64), Image.Resampling.LANCZOS)


# Main


def main() -> None:
    manifest = yaml.safe_load((SPRITES_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    entries = manifest["animations"]
    preview_rows = []
    for name, make in ANIMATIONS.items():
        poses = make()
        expected = entries[name]["frames"]
        if len(poses) != expected:
            raise SystemExit(f"{name}: generated {len(poses)} frames, manifest says {expected}")
        strip = Image.new("RGBA", (FW * len(poses), FH), (0, 0, 0, 0))
        for i, pose in enumerate(poses):
            strip.paste(draw_cat(pose), (i * FW, 0))
        strip.save(SPRITES_DIR / f"{name}.png", optimize=True)
        preview_rows.append((name, strip))
        print(f"  {name:12s} {len(poses):2d} frames")

    BASKET_DIR.mkdir(parents=True, exist_ok=True)
    back, front, meta = make_basket()
    back.save(BASKET_DIR / meta["back"], optimize=True)
    front.save(BASKET_DIR / meta["front"], optimize=True)
    (BASKET_DIR / "basket.yaml").write_text(
        "# Generated by tools_dev/make_placeholder_sprites.py (edit freely).\n"
        + yaml.safe_dump(meta, sort_keys=False),
        encoding="utf-8",
        newline="\n",  # same line endings on every OS (no git churn)
    )

    BUBBLE_DIR.mkdir(parents=True, exist_ok=True)
    for name, img in make_mood_icons().items():
        img.save(BUBBLE_DIR / f"{name}.png")

    ICON_DIR.mkdir(parents=True, exist_ok=True)
    make_icon().save(ICON_DIR / "tray.png")

    # contact sheet for eyeballing: docs/sprite_preview.png (half size, light background)
    cell = FW // 2
    cols = max(len(s.width // FW * [0]) for _, s in preview_rows)
    sheet = Image.new(
        "RGBA", (cell * (cols + 1), cell * (len(preview_rows) + 1)), (205, 214, 226, 255)
    )
    d = ImageDraw.Draw(sheet)
    for r, (name, strip) in enumerate(preview_rows):
        d.text((6, r * cell + cell // 2 - 6), name, fill=(30, 30, 40, 255))
        small = strip.resize((strip.width // 2, cell), Image.Resampling.LANCZOS)
        sheet.alpha_composite(small, (cell, r * cell))
    basket_row = len(preview_rows) * cell
    d.text((6, basket_row + cell // 2 - 6), "basket", fill=(30, 30, 40, 255))
    bk = Image.alpha_composite(back, front).resize((BW // 2, BH // 2))
    sheet.alpha_composite(bk, (cell, basket_row + 10))
    curled = draw_cat(Pose("curl", eye_style="sleep", mouth="none"))
    combo = back.copy()
    bed = meta["bed"]
    combo.alpha_composite(curled, (bed[0] - AX, bed[1] - AY))
    combo.alpha_composite(front)
    sheet.alpha_composite(combo.resize((BW // 2, BH // 2)), (cell * 3, basket_row + 10))
    sheet.save(ROOT / "docs" / "sprite_preview.png")
    print("Wrote sprites, basket, tray icon and docs/sprite_preview.png")


if __name__ == "__main__":
    main()

r"""Draw the bubble icons for tools (camera, note, timer, music).

.venv\Scripts\python tools_dev\make_tool_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "bubbles"
SS = 4


def S(v: float) -> float:  # noqa: N802 - same helper name as make_placeholder_sprites
    return v * SS


def outlined(draw_fn, color, detail=None) -> Image.Image:
    size = 64 * SS
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    draw_fn(d, (255, 255, 255, 255), S(3.2))  # fat white = the outline
    draw_fn(d, color, 0)
    if detail is not None:
        detail(d)
    return img.resize((64, 64), Image.Resampling.LANCZOS)


def camera(d, c, g):
    d.rounded_rectangle([S(6) - g, S(20) - g, S(58) + g, S(54) + g], radius=S(7), fill=c)
    d.rounded_rectangle([S(20) - g, S(12) - g, S(36) + g, S(24) + g], radius=S(3), fill=c)


def camera_detail(d):
    d.ellipse([S(21), S(25), S(43), S(47)], fill=(255, 255, 255, 255))
    d.ellipse([S(25), S(29), S(39), S(43)], fill=(70, 80, 110, 255))
    d.ellipse([S(47), S(25), S(53), S(31)], fill=(255, 220, 120, 255))


def note(d, c, g):
    d.polygon([(S(8) - g, S(8) - g), (S(56) + g, S(8) - g), (S(56) + g, S(44) + g),
               (S(42) + g, S(58) + g), (S(8) - g, S(58) + g)], fill=c)  # fmt: skip


def note_detail(d):
    d.polygon([(S(56), S(44)), (S(42), S(44)), (S(42), S(58))], fill=(230, 196, 70, 255))
    for y in (20, 30, 40):
        d.line([(S(15), S(y)), (S(44 if y < 40 else 34), S(y))], fill=(150, 120, 40, 255),
               width=int(S(3)))  # fmt: skip


def timer(d, c, g):
    d.ellipse([S(8) - g, S(12) - g, S(56) + g, S(60) + g], fill=c)
    d.rounded_rectangle([S(26) - g, S(3) - g, S(38) + g, S(12) + g], radius=S(2), fill=c)


def timer_detail(d):
    d.ellipse([S(14), S(18), S(50), S(54)], fill=(255, 255, 255, 255))
    d.line([(S(32), S(36)), (S(32), S(23))], fill=(200, 70, 70, 255), width=int(S(3.5)))
    d.line([(S(32), S(36)), (S(42), S(42))], fill=(70, 80, 110, 255), width=int(S(3.5)))
    d.ellipse([S(29), S(33), S(35), S(39)], fill=(70, 80, 110, 255))


def music(d, c, g):
    w = int(S(6) + 2 * g)
    d.line([(S(24), S(46)), (S(24), S(10)), (S(52), S(4)), (S(52), S(40))], fill=c, width=w,
           joint="curve")  # fmt: skip
    d.ellipse([S(10) - g, S(40) - g, S(28) + g, S(56) + g], fill=c)
    d.ellipse([S(38) - g, S(34) - g, S(56) + g, S(50) + g], fill=c)


def main() -> int:
    icons = {
        "camera": outlined(camera, (110, 125, 165, 255), camera_detail),
        "note": outlined(note, (255, 215, 90, 255), note_detail),
        "timer": outlined(timer, (235, 105, 95, 255), timer_detail),
        "music": outlined(music, (160, 110, 210, 255)),
    }
    for name, img in icons.items():
        img.save(OUT / f"{name}.png")
        print("wrote", OUT / f"{name}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

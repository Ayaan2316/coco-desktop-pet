r"""Pack a folder of rendered frames into one sprite strip.

    .venv\Scripts\python tools_dev\pack_frames.py renders\walk cats\coco\sprites\walk.png
    .venv\Scripts\python tools_dev\pack_frames.py renders\walk walk.png --size 256 --flip

Frames are read in name order and placed bottom-centre. --flip mirrors right-facing renders.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("frames_dir", type=Path)
    ap.add_argument("out_png", type=Path)
    ap.add_argument("--size", type=int, default=256, help="frame width/height (default 256)")
    ap.add_argument("--flip", action="store_true", help="mirror frames (renders face right)")
    ap.add_argument("--fps", type=float, default=12)
    args = ap.parse_args()

    files = sorted(p for p in args.frames_dir.iterdir() if p.suffix.lower() == ".png")
    if not files:
        raise SystemExit(f"No .png frames in {args.frames_dir}")

    size = args.size
    strip = Image.new("RGBA", (size * len(files), size), (0, 0, 0, 0))
    for i, path in enumerate(files):
        img = Image.open(path).convert("RGBA")
        if args.flip:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        x = i * size + (size - img.width) // 2
        y = size - img.height  # bottom-aligned
        strip.alpha_composite(img, (x, y))
    args.out_png.parent.mkdir(parents=True, exist_ok=True)
    strip.save(args.out_png, optimize=True)

    name = args.out_png.stem
    print(f"Wrote {args.out_png} ({len(files)} frames of {size}x{size})")
    print("manifest.yaml entry:")
    print(f"  {name}: {{frames: {len(files)}, fps: {args.fps:g}, loop: true}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

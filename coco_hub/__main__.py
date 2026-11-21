"""Allows `python -m coco_hub`."""

from coco_hub.main import main

if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())

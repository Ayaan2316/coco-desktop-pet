"""Shared dashboard state: auth, the link to Coco, the store and a status poller."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import yaml
from nicegui import run

from coco.core.paths import PROJECT_ROOT, user_data_dir
from coco.tools.store import Store
from coco_hub.auth import PinAuth
from coco_hub.coco_link import CocoLink

log = logging.getLogger(__name__)

auth = PinAuth()
coco = CocoLink()
store = Store(user_data_dir() / "coco.db")
SPRITES = PROJECT_ROOT / "cats" / "coco" / "sprites"

# {"running": bool, "status": dict | None, "busy": "starting" | "stopping" | "", "at": time}
snapshot: dict = {"running": False, "status": None, "busy": "", "at": 0.0}


def sprite_anims(folder: Path = SPRITES) -> dict[str, tuple[int, float]]:
    """{animation: (frames, fps)} from her sprite manifest."""
    try:
        data = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {"sit": (8, 6.0), "sleep": (6, 4.0)}
    return {
        name: (int(spec.get("frames", 1)), float(spec.get("fps", 6)))
        for name, spec in (data.get("animations") or {}).items()
        if (folder / f"{name}.png").exists()
    }


ANIMS = sprite_anims()


async def refresh() -> None:
    status = await run.io_bound(coco.status)
    snapshot.update(running=status is not None, status=status, at=time.time())


async def poll_forever() -> None:
    while True:
        try:
            await refresh()
        except Exception:  # noqa: BLE001 - keep polling whatever happens
            log.exception("status poll failed")
        await asyncio.sleep(1.5)

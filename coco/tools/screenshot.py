"""Screenshots and "explain my screen"."""

from __future__ import annotations

import base64
import io
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from coco.core.geometry import Rect
from coco.tools.intents import intent
from coco.tools.registry import ToolCall, tool

log = logging.getLogger(__name__)


def shot_name(now: datetime) -> str:
    return f"Coco_{now:%Y-%m-%d_%H-%M-%S}.png"


def shots_dir(folder: str = "") -> Path:
    if folder:
        return Path(folder)
    from coco.desktop.windows import local_folder

    return local_folder("Pictures") / "Coco"


def cleanup_old(folder: Path, keep_days: float, now: float | None = None) -> int:
    """Delete Coco's own screenshots older than keep_days (0 = keep forever)."""
    if keep_days <= 0 or not folder.exists():
        return 0
    cutoff = (now or time.time()) - keep_days * 86400
    removed = 0
    for f in folder.glob("Coco_*.png"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


def capture(rect: Rect):
    """Grab a screen rectangle (physical pixels) -> PIL image."""
    import mss
    from PIL import Image

    box = {"left": int(rect.left), "top": int(rect.top),
           "width": max(1, int(rect.width)), "height": max(1, int(rect.height))}  # fmt: skip
    with mss.mss() as sct:
        raw = sct.grab(box)
    return Image.frombytes("RGB", raw.size, raw.rgb)


def to_b64_png(image, max_side: int = 1280) -> str:
    """A smaller PNG for the AI (big screenshots cost VRAM and time)."""
    image = image.copy()
    image.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --- the tools
@tool(
    "explain_screen",
    "look at their screen (the active window) and explain it, e.g. an error message",
    needs_qt=True,
    # (not plain "this"/"that": "...take care of that, what should I do?" isn't about the
    # screen)
    words=r"screen|window|error|explain th|look at th|see th|read th|what'?s th|what is th|"
    r"dekh|kya hai|kya likha",
    label="Explain my screen",
)
def explain_screen(call: ToolCall, ctx):
    ctx.screen.look(call)  # reports back through ctx.report when the picture is ready


@tool(
    "screenshot",
    "take a screenshot (target: screen | window | area)",
    needs_qt=True,
    words=r"screen ?shot|screen|capture|snap|photo|picture|print ?screen|tasveer",
    label="Screenshots",
)
def screenshot(call: ToolCall, ctx):
    ctx.screen.take(call)


# --- phrases
def _target(m: re.Match, said: str) -> dict[str, str]:
    if re.search(r"\b(area|region|part|portion|select|selection|crop|hissa|thoda sa)\b", said):
        return {"target": "area"}
    if re.search(r"\b(window|this app|app ka|is app|is window|current app)\b", said):
        return {"target": "window"}
    return {"target": "screen"}


# explain first: "screenshot this and explain it" is an explanation
intent(
    "explain_screen",
    r"\b(explain|read|look at|check|see|what'?s on|what is on|describe)\b.*\b(screen|window)\b",
    r"\b(screen ?shot|screen)\b.*\b(explain|tell me what|what'?s|what is)\b",
    r"\bwhat'?s (this|that) error\b|\bwhat is (this|that) error\b",
    r"\b(this|that) error\b.*\b(mean|fix|solve)\b|\bfix (this|that) error\b",
    r"^(ye|yeh|ya) (kya hai|error kya hai|kya likha hai)\b|\bscreen (pe|par) kya hai\b",
    r"\b(ye|yeh|is) error\b",
    r"\bwhat am i looking at\b|\bwhat'?s (?:this|that) on (?:my )?screen\b",
    questions=True,
)
intent(
    "screenshot",
    r"\b(take|grab|capture|snap|get|make|save)\b.{0,20}\b(screen ?shot|screen ?cap|snapshot)\b",
    r"\b(capture|snap)\b.{0,12}\b(screen|window|area)\b",
    r"^(screen ?shot|print ?screen)\b",
    r"\bscreen ?shot\b.{0,12}\b(lo|le lo|lena|le|kheencho|khicho|nikalo)\b",
    r"\bscreen ka (photo|screenshot|picture)\b",
    args=_target,
)

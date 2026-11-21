"""Screenshots: phrases (English + Hinglish), names, auto-delete, the AI copy."""

import base64
import io
import os
import time
from datetime import datetime

import pytest
from PIL import Image

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.tools.intents import parse_task
from coco.tools.screenshot import cleanup_old, shot_name, to_b64_png

TOOLS = ["screenshot", "explain_screen"]


@pytest.mark.parametrize(
    ("said", "tool", "target"),
    [
        ("Coco, take a screenshot", "screenshot", "screen"),
        ("take a screenshot please", "screenshot", "screen"),
        ("can you grab a screen shot of this window", "screenshot", "window"),
        ("screenshot", "screenshot", "screen"),
        ("capture my screen", "screenshot", "screen"),
        ("take a screenshot of an area", "screenshot", "area"),
        ("screenshot lo", "screenshot", "screen"),
        ("Coco screenshot le lo is window ka", "screenshot", "window"),
        ("screen ka photo lo", "screenshot", "screen"),
        ("what's this error?", "explain_screen", None),
        ("Coco, explain my screen", "explain_screen", None),
        ("what's on my screen", "explain_screen", None),
        ("screenshot this and explain it", "explain_screen", None),
        ("how do I fix this error", "explain_screen", None),
        ("ye kya hai?", "explain_screen", None),
        ("ye error kya hai", "explain_screen", None),
        ("screen pe kya hai", "explain_screen", None),
    ],
)
def test_phrases(said, tool, target):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == tool, said
    if target:
        assert call.args["target"] == target


@pytest.mark.parametrize(
    "said",
    [
        "what is a capacitor?",
        "my screen is so dirty lol",
        "I love you coco",
        "tell me a joke",
        "how do screenshots work on windows?",
    ],
)
def test_not_a_screenshot(said):
    assert parse_task(said, TOOLS) is None, said


def test_switched_off_tools_are_not_matched():
    assert parse_task("take a screenshot", ["explain_screen"]) is None


def test_file_name():
    assert shot_name(datetime(2026, 11, 21, 15, 30, 12)) == "Coco_2026-11-21_15-30-12.png"


def test_auto_delete_only_old_coco_shots(tmp_path):
    old = tmp_path / "Coco_2026-01-01_00-00-00.png"
    new = tmp_path / "Coco_2026-11-21_00-00-00.png"
    mine = tmp_path / "holiday.png"  # not hers: never touched
    for f in (old, new, mine):
        f.write_bytes(b"x")
    now = time.time()
    os.utime(old, (now - 10 * 86400, now - 10 * 86400))
    os.utime(mine, (now - 10 * 86400, now - 10 * 86400))
    assert cleanup_old(tmp_path, keep_days=0, now=now) == 0  # 0 = keep forever
    assert cleanup_old(tmp_path, keep_days=7, now=now) == 1
    assert not old.exists() and new.exists() and mine.exists()


def test_ai_copy_is_small():
    img = Image.new("RGB", (2560, 1600), (30, 30, 30))
    b64 = to_b64_png(img, max_side=1280)
    small = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert max(small.size) == 1280 and img.size == (2560, 1600)  # original untouched

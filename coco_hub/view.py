"""Formats Coco's status for the dashboard."""

from __future__ import annotations

DOING = {
    "sit": "sitting", "purr": "purring", "sleep": "napping", "walk": "strolling",
    "run": "zooming around", "fall": "falling", "jump": "jumping", "dangle": "being carried",
    "stalk": "stalking your cursor", "pounce": "pouncing", "peek": "peeking at you",
    "peek_hide": "peeking at you", "think": "thinking", "talk": "talking to you",
    "emote": "showing how she feels", "sulk": "sulking",
}  # fmt: skip
WHERE = {"bed": "in her bed", "window": "on top of a window", "ground": "on the taskbar"}
STATE_ANIM = {"sulk": "sad", "emote": "happy", "peek_hide": "peek", "dangle": "dangle"}

# (key, icon, name, kind) : kind "listen" glows blue instead of lamp-orange
TILES = [
    ("visible", "visibility", "On screen", ""),
    ("voice", "record_voice_over", "Voice", ""),
    ("wake", "hearing", "Hey Coco", "listen"),
    ("mic_ready", "mic", "Mic ready", "listen"),
    ("paused", "pets", "Wandering", ""),
    ("focus", "timer", "Focus", ""),
]


def describe(s: dict | None) -> tuple[str, str]:
    """(status line HTML, smaller line) for the snapshot."""
    if s is None:
        return ("Coco is <em>off</em>", "Pull the lamp cord to wake her up.")
    if not s.get("visible"):
        return ("Coco is <em>hidden</em>", "She's still running. Show her with “On screen”.")
    if s.get("fullscreen_hidden"):
        return ("Coco is <em>waiting</em>", "You're in a full-screen app, so she stepped away.")
    doing = DOING.get(s.get("state", ""), "around")
    where = WHERE.get(s.get("surface", ""), "")
    feels = (s.get("mood") or {}).get("words") or ""
    return (f"Coco is <em>{doing}</em>{' ' + where if where else ''}",
            f"She feels {feels}." if feels else "")  # fmt: skip


def sprite_for(s: dict | None, anims) -> str:
    if s is None:
        return "sleep"
    for name in (s.get("anim"), STATE_ANIM.get(s.get("state", "")), s.get("state")):
        if name in anims:
            return name
    return "sit"


def tile_state(key: str, s: dict) -> tuple[bool | None, str]:
    """(on?, words) for a switch; None = not available on this PC."""
    if key == "focus":
        f = s.get("focus") or {}
        if not f.get("active"):
            return False, "Off"
        left = int(f.get("remaining", 0))
        return True, f"{'Work' if f.get('working') else 'Break'} {left // 60}:{left % 60:02d}"
    if key == "paused":
        return (not s.get("paused")), ("Roaming" if not s.get("paused") else "Staying put")
    value = s.get(key)
    if value is None:
        return None, "Not set up"
    return bool(value), "On" if value else "Off"

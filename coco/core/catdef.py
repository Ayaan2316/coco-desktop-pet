"""Loads a cat definition from cats/<name>/cat.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from coco.core.paths import CATS_DIR


@dataclass
class CatDef:
    name: str
    display_name: str
    folder: Path
    size: str | None = None  # "S" / "M" / "L"; None = config default
    home_corner: str = "bottom_right"  # where the bed lives: bottom_right | bottom_left
    home_margin: int = 16  # logical px between bed and the work-area edge
    # Her body box as a fraction of the sprite frame (used for wall bounces + walking room).
    collision_width: float = 0.55
    collision_height: float = 0.6
    behaviour_weights: dict[str, float] = field(default_factory=dict)  # overrides config
    personality: str = ""  # system prompt for the AI brain
    voice: dict[str, Any] = field(default_factory=dict)  # TTS settings
    allowed_tools: list[str] = field(default_factory=list)  # tool allow-list
    # AI emotion -> animation name (missing ones fall back to the emotion's own
    # name, then to "sit"), and canned lines she can say without the AI.
    emotions: dict[str, str] = field(default_factory=dict)
    lines: dict[str, list[str] | str] = field(default_factory=dict)
    # What she says when given a command (or why she can't).
    command_replies: dict[str, list[str] | str] = field(default_factory=dict)
    # the same in Hinglish: entries are "text" or {text: Hinglish, say: Devanagari}
    command_replies_hinglish: dict[str, Any] = field(default_factory=dict)

    def emotion_anim(self, emotion: str | None) -> str:
        if not emotion or emotion == "neutral":
            return self.emotions.get("neutral", "sit")
        return self.emotions.get(emotion, emotion)

    @property
    def sprites_dir(self) -> Path:
        return self.folder / "sprites"

    @property
    def manifest_path(self) -> Path:
        return self.sprites_dir / "manifest.yaml"


def personalize(text: str, owner: str) -> str:
    """Fill in {name} (your name, config.yaml owner_name) in her lines and personality."""
    return text.replace("{name}", owner)


def load_cat(name: str, cats_dir: Path = CATS_DIR) -> CatDef:
    folder = cats_dir / name
    path = folder / "cat.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No cat definition at {path}")
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    home = raw.get("home", {}) or {}
    collision = raw.get("collision", {}) or {}
    return CatDef(
        name=raw.get("name", name),
        display_name=raw.get("display_name", name.title()),
        folder=folder,
        size=raw.get("size"),
        home_corner=home.get("corner", "bottom_right"),
        home_margin=int(home.get("margin", 16)),
        collision_width=float(collision.get("width", 0.55)),
        collision_height=float(collision.get("height", 0.6)),
        behaviour_weights=dict(raw.get("behaviour_weights", {}) or {}),
        personality=raw.get("personality", "") or "",
        voice=dict(raw.get("voice", {}) or {}),
        allowed_tools=list(raw.get("allowed_tools", []) or []),
        emotions={str(k): str(v) for k, v in (raw.get("emotions") or {}).items()},
        lines=dict(raw.get("lines", {}) or {}),
        command_replies=dict(raw.get("command_replies", {}) or {}),
        command_replies_hinglish=dict(raw.get("command_replies_hinglish", {}) or {}),
    )

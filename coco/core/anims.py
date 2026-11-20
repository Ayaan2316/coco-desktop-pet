"""Sprite manifest: animations, frame counts, speeds and anchors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# Core animations. The manifest must define all of them.
REQUIRED_ANIMS = (
    "sit",
    "blink",
    "tail_swish",
    "loaf",
    "groom",
    "yawn",
    "stretch",
    "look_around",
    "walk",
    "run",
    "jump",
    "fall",
    "land",
    "sleep",
    "wake_up",
    "dangle",
    "purr",
    "stalk",
    "pounce",
    "peek",
)


# Talking and the emotions the AI can pick (cat.yaml `emotions:` maps to these).
# Optional: a cat without them still works, she just shows `sit` instead.
TALK_EMOTION_ANIMS = (
    "talk",
    "think",
    "happy",
    "sad",
    "angry",
    "surprised",
    "curious",
    "sleepy",
    "love",
)


@dataclass(frozen=True)
class AnimSpec:
    name: str
    file: str
    frames: int
    fps: float
    loop: bool
    frame_w: int
    frame_h: int
    anchor: tuple[int, int]  # paw contact point inside a frame (source pixels)
    grab: tuple[int, int] | None = None  # where the mouse holds her (dangle only)
    next: str | None = None  # suggested animation after a one-shot finishes

    @property
    def duration(self) -> float:
        """Seconds for one pass through all frames."""
        return self.frames / self.fps

    def frame_at(self, t: float) -> int:
        """Frame index to show `t` seconds after the animation started."""
        index = int(max(t, 0.0) * self.fps)
        if self.loop:
            return index % self.frames
        return min(index, self.frames - 1)

    def time_to_next_frame(self, t: float) -> float:
        """Seconds until the visible frame changes (inf once a one-shot has finished)."""
        if not self.loop and t >= self.duration:
            return float("inf")
        period = 1.0 / self.fps
        return period - (max(t, 0.0) % period)


@dataclass(frozen=True)
class SpriteManifest:
    folder: Path
    anims: dict[str, AnimSpec]

    def __getitem__(self, name: str) -> AnimSpec:
        return self.anims[name]

    def missing(self, include_emotions: bool = False) -> list[str]:
        wanted = REQUIRED_ANIMS + (TALK_EMOTION_ANIMS if include_emotions else ())
        return [a for a in wanted if a not in self.anims]


def _pair(value: object, what: str) -> tuple[int, int]:
    if not (isinstance(value, list | tuple) and len(value) == 2):
        raise ValueError(f"{what} must be a list of two numbers, got {value!r}")
    return int(value[0]), int(value[1])


def load_manifest(path: Path) -> SpriteManifest:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    default_size = _pair(raw.get("frame_size", [256, 256]), "frame_size")
    default_anchor = _pair(raw.get("anchor", [128, 236]), "anchor")

    anims: dict[str, AnimSpec] = {}
    for name, entry in (raw.get("animations") or {}).items():
        entry = entry or {}
        w, h = _pair(entry.get("frame_size", default_size), f"{name}.frame_size")
        frames = int(entry.get("frames", 1))
        fps = float(entry.get("fps", 8))
        if frames < 1 or fps <= 0:
            raise ValueError(f"animation {name}: frames must be >= 1 and fps > 0")
        grab = entry.get("grab")
        anims[name] = AnimSpec(
            name=name,
            file=entry.get("file", f"{name}.png"),
            frames=frames,
            fps=fps,
            loop=bool(entry.get("loop", True)),
            frame_w=w,
            frame_h=h,
            anchor=_pair(entry.get("anchor", default_anchor), f"{name}.anchor"),
            grab=_pair(grab, f"{name}.grab") if grab is not None else None,
            next=entry.get("next"),
        )
    return SpriteManifest(folder=path.parent, anims=anims)

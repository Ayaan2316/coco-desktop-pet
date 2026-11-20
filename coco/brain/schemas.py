"""Data the brain puts on the event bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Emotions the AI may return. cat.yaml maps each to an animation (`emotions:`).
EMOTIONS = ("neutral", "happy", "sad", "angry", "surprised", "curious", "sleepy", "love")


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainReply:
    text: str
    emotion: str | None = None  # None = not known yet (still streaming)
    action: str = "none"  # something she should do (see behaviour/commands.py)
    tool_calls: list[ToolCall] = field(default_factory=list)
    final: bool = True
    request_id: int = 0
    hindi: bool = False  # you spoke Hinglish: her voice reads `speech` (Hindi voice)
    tool: str = "none"  # a PC task the AI chose (see coco/tools/registry.py)
    tool_args: dict[str, str] = field(default_factory=dict)
    speech: str = ""  # the same reply in Devanagari, for her Hindi voice only
    remember: str = ""  # a fact about you she wants to keep (memory.py filters)


@dataclass
class BrainStatus:
    """Published on Topic.BRAIN_STATUS: what the brain is doing / what went wrong."""

    state: str  # "thinking" | "ready" | "offline" | "model_missing" | "error"
    message: str = ""
    request_id: int = 0

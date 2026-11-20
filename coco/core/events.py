"""Event types used on the bus."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


class Topic:
    # --- input: mouse interaction with Coco (published by ui/cat_window.py)
    DRAG_START = "input.drag_start"  # payload: None (engine reads the cursor itself)
    DRAG_END = "input.drag_end"  # payload: None
    CLICK = "input.click"  # payload: Point (physical px)
    HOVER = "input.hover"  # payload: Point; cursor moving over her body (petting input)

    # --- desktop: published by desktop/sensors.py
    FULLSCREEN = "desktop.fullscreen"  # payload: bool (a fullscreen app is active)
    DISPLAY_CHANGED = "desktop.display_changed"  # payload: None (monitors/DPI changed)

    # --- cat: published by the behaviour engine
    STATE_CHANGED = "cat.state_changed"  # payload: {"old": str, "new": str}
    PETTED = "cat.petted"  # payload: None

    # --- ui: tray menu + hotkeys
    COMMAND = "ui.command"  # payload: {"name": str, ...}

    # --- chat: the speech bubble
    CHAT_OPEN = "chat.open"  # payload: None (click on Coco / chat hotkey)
    CHAT_ASK = "chat.ask"  # payload: str (the question you typed)
    CHAT_CLOSE = "chat.close"  # payload: None
    CHAT_SAY = "chat.say"  # payload: {"text", "emotion", "animate"=True} canned line, no AI
    COMMAND_DONE = "cat.command_done"  # payload: {"action", "ok": bool, "reason": str}

    # --- voice (posted from audio threads)
    VOICE_START = "voice.start"  # payload: None - she started speaking out loud
    VOICE_END = "voice.end"  # payload: None - she finished everything she had to say
    LISTEN_START = "voice.listen_start"  # payload: None - the mic is open (ear icon)
    LISTEN_END = "voice.listen_end"  # payload: {"cancelled": bool} - the mic is closed
    HEARD = "voice.heard"  # payload: {"ok": bool, "text": str, "reason": str}

    # --- brain (AI replies)
    BRAIN_REPLY = "brain.reply"  # payload: coco.brain.schemas.BrainReply (posted)
    BRAIN_STATUS = "brain.status"  # payload: coco.brain.schemas.BrainStatus (posted)

    # --- tools (posted from tool threads)
    TOOL_RESULT = "tool.result"  # payload: (ToolCall, ToolResult)


@dataclass(slots=True)
class Event:
    topic: str
    payload: Any = None
    timestamp: float = field(default_factory=time.monotonic)

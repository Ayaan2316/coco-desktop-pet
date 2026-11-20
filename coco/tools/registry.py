"""Tool registry and allow-list."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

RISK_LEVELS = ("safe", "confirm")  # "confirm" = ask the user in a Yes/No bubble first
ARG_NAMES = ("target", "text", "when", "amount")  # what the AI may fill in (strings)


@dataclass
class ToolCall:
    tool: str
    args: dict[str, str] = field(default_factory=dict)
    said: str = ""  # what you said / typed
    lang: str = "en"  # "hinglish": reply in Hinglish
    source: str = "local"  # "local" (phrase matcher) or "ai" (the brain chose it)

    def arg(self, name: str, default: str = "") -> str:
        return str(self.args.get(name) or default).strip()


@dataclass
class ToolResult:
    ok: bool = True
    say: str = ""  # what she says (bubble + voice)
    speech_hi: str = ""  # Devanagari for her Hindi voice (Hinglish replies)
    facts: str = ""  # info tools: facts the AI turns into a reply in her own words
    emotion: str = "happy"
    image_b64: str = ""  # explain-my-screen: the picture for the AI
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str  # one line for her prompt ("what it does (args)")
    func: Callable[[ToolCall, Any], ToolResult]
    risk: str = "safe"
    needs_qt: bool = False  # True: must run on the Qt main thread (screen capture, clipboard)
    words: re.Pattern | None = None
    label: str = ""  # tray menu text
    confirm_if: Callable[[ToolCall], bool] | None = None  # risky only for some calls
    confirm_text: Callable[[ToolCall], str] | None = None  # the Yes/No question

    def needs_confirm(self, call: ToolCall) -> bool:
        return self.risk == "confirm" or bool(self.confirm_if and self.confirm_if(call))


class Registry:
    def __init__(self) -> None:
        self.specs: dict[str, ToolSpec] = {}

    def tool(
        self,
        name: str,
        description: str,
        *,
        risk: str = "safe",
        needs_qt: bool = False,
        words: str = "",
        label: str = "",
        confirm_if: Callable[[ToolCall], bool] | None = None,
        confirm_text: Callable[[ToolCall], str] | None = None,
    ) -> Callable:
        if risk not in RISK_LEVELS:
            raise ValueError(f"risk must be one of {RISK_LEVELS}")

        def register(func: Callable[[ToolCall, Any], ToolResult]) -> Callable:
            pattern = re.compile(rf"(?:{words})", re.IGNORECASE) if words else None
            self.specs[name] = ToolSpec(
                name,
                description,
                func,
                risk,
                needs_qt,
                pattern,
                label or name,
                confirm_if,
                confirm_text,
            )
            return func

        return register

    def available(self, allowed: list[str], disabled: list[str] | None = None) -> list[str]:
        """Tools that exist, are in cat.yaml's allowed_tools and aren't off in the tray."""
        off = set(disabled or ())
        return [n for n in self.specs if n in allowed and n not in off]

    def is_allowed(self, name: str, allowed: list[str], disabled: list[str] | None = None) -> bool:
        return name in self.available(allowed, disabled)

    def supported_by_words(self, name: str, said: str) -> bool:
        """Guard for AI-chosen tools: does what you said fit this tool at all?"""
        spec = self.specs.get(name)
        if spec is None:
            return False
        return spec.words is None or bool(spec.words.search(said))

    def describe(self, names: list[str]) -> str:
        return "\n".join(f"  {n}: {self.specs[n].description}" for n in names)

    def run(self, call: ToolCall, ctx: Any, allowed: list[str], disabled=None) -> ToolResult:
        if not self.is_allowed(call.tool, allowed, disabled):
            raise PermissionError(f"Tool {call.tool!r} is not on the allow-list")
        return self.specs[call.tool].func(call, ctx)


REGISTRY = Registry()
tool = REGISTRY.tool


def is_allowed(name: str, allowed: list[str]) -> bool:  # (kept for older callers)
    return REGISTRY.is_allowed(name, allowed)

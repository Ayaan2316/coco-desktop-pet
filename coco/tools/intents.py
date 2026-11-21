"""Phrase rules that turn clear requests into tool calls without the AI."""

from __future__ import annotations

import re
from collections.abc import Callable

from coco.core.hindi import looks_hinglish, to_roman
from coco.tools.registry import ToolCall

Builder = Callable[[re.Match, str], dict[str, str] | None]
_INTENTS: list[tuple[int, str, re.Pattern, Builder | None, bool]] = []

# A question about something isn't a request to do it ("how do screenshots work?")
QUESTION = re.compile(
    r"^(?:how|what|why|when|where|who|which|does|do you|is|are|was|were|should|"
    r"kya|kyun|kyon|kaise|kab|kahan|kaun)\b"
)

# "Hey Coco, could you please ..." / "Coco zara ..." in front of the request
_LEAD = re.compile(
    r"^(?:(?:hey|hi|ok|okay|oi|yo|arre|arey)\s+)?(?:(?:coco|koko)\b[\s,]*)?"
    r"(?:(?:please|pls|can you|could you|would you|will you|can u|just|quickly|"
    r"zara|jaldi|ab|abhi|thoda)\s+)*",
    re.IGNORECASE,
)


def clean(text: str) -> str:
    """Lower-case, romanised, without her name/fillers in front; keeps digits and ':'."""
    text = to_roman(text).lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9:' ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _LEAD.sub("", text).strip()


def intent(
    tool: str,
    *patterns: str,
    args: Builder | None = None,
    questions: bool = False,
    priority: int = 0,
) -> None:
    """Register phrases for `tool`. `args(match, cleaned_text)` -> the call's arguments
    (return None to reject the match after all). questions=True: also match sentences
    that start like a question ("what's this error?" asks to look at the screen).
    Higher priority is tried first; catch-alls ("open X", "search X") use a negative one."""
    for p in patterns:
        _INTENTS.append((priority, tool, re.compile(p), args, questions))
    _INTENTS.sort(key=lambda item: -item[0])  # stable: same priority keeps its order


def parse_task(text: str, available: list[str] | None = None) -> ToolCall | None:
    """The tool a clear request asks for, or None (let the AI handle it)."""
    said = clean(text)
    if not said:
        return None
    is_question = bool(QUESTION.match(said))
    for _priority, tool, pattern, builder, questions in _INTENTS:
        if available is not None and tool not in available:
            continue
        if is_question and not questions:
            continue
        m = pattern.search(said)
        if not m:
            continue
        args = builder(m, said) if builder else {}
        if args is None:
            continue
        lang = "hinglish" if looks_hinglish(text) else "en"
        return ToolCall(tool, {k: v for k, v in args.items() if v}, said=text, lang=lang)
    return None


# --- yes / no
_YES = re.compile(
    r"^(?:yes|yeah|yep|yup|ya|sure|ok|okay|do it|go ahead|confirm|delete it|"
    r"haan|haa|han|ha|ji|haan ji|kar do|karo|theek hai|thik hai|bilkul)\b"
)
_NO = re.compile(
    r"^(?:no|nope|nah|cancel|don't|dont|stop|never mind|leave it|"
    r"nahi|nahin|na|mat|mat karo|rehne do|ruko)\b"
)


def is_yes(text: str) -> bool:
    return bool(_YES.match(clean(text)))


def is_no(text: str) -> bool:
    return bool(_NO.match(clean(text)))

"""Focus mode (Pomodoro)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool


@dataclass
class Phase:
    kind: str  # "work" | "break"
    ends: float  # unix time
    round: int  # 1, 2, 3...


class FocusSession:
    def __init__(self, work_min: float = 25, break_min: float = 5, clock=time.time) -> None:
        self.default_work = work_min
        self.break_min = break_min
        self.clock = clock
        self.phase: Phase | None = None
        self.work_min = work_min

    @property
    def active(self) -> bool:
        return self.phase is not None

    @property
    def working(self) -> bool:
        return self.phase is not None and self.phase.kind == "work"

    def start(self, minutes: float | None = None) -> Phase:
        self.work_min = minutes or self.default_work
        self.phase = Phase("work", self.clock() + self.work_min * 60, 1)
        return self.phase

    def stop(self) -> Phase | None:
        old, self.phase = self.phase, None
        return old

    def remaining(self) -> float:
        return max(0.0, self.phase.ends - self.clock()) if self.phase else 0.0

    def advance(self) -> Phase | None:
        """The current phase ended: work -> break -> work (next round)..."""
        if self.phase is None:
            return None
        p = self.phase
        if p.kind == "work":
            self.phase = Phase("break", self.clock() + self.break_min * 60, p.round)
        else:
            self.phase = Phase("work", self.clock() + self.work_min * 60, p.round + 1)
        return self.phase


def clock_text(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m:02d}:{s:02d}"


# --- the tool
def _r(lang, en, hinglish, devanagari="", ok=True, emotion="happy", **data) -> ToolResult:
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion, data=data)
    return ToolResult(ok=ok, say=en, emotion=emotion, data=data)


@tool(
    "focus",
    "focus mode / pomodoro (target: start | stop | status; amount: work minutes)",
    words=r"focus|pomodoro|concentrat|study|padhai|deep work|work mode",
    label="Focus mode",
)
def focus(call: ToolCall, ctx) -> ToolResult:
    session: FocusSession = ctx.focus
    lang = call.lang
    action = call.arg("target", "start").lower()
    if action == "stop":
        if not session.active:
            return _r(lang, "Focus mode isn't on~", "Focus mode chal hi nahi raha~",
                      "फोकस मोड चल ही नहीं रहा।", emotion="neutral")  # fmt: skip
        session.stop()
        return _r(lang, "Focus mode off. Good work today! *stretches*",
                  "Focus mode band. Bahut accha kaam kiya! *stretches*",
                  "फोकस मोड बंद। बहुत अच्छा काम किया!", focus="stop")  # fmt: skip
    if action == "status":
        if not session.active:
            return _r(lang, "Focus mode is off. Say 'start focus' when you're ready~",
                      "Focus mode band hai. 'start focus' bolo jab ready ho~",
                      "फोकस मोड बंद है।", emotion="neutral")  # fmt: skip
        left = clock_text(session.remaining())
        what = "work" if session.working else "break"
        what_hi = "kaam" if session.working else "break"
        return _r(lang, f"{left} left in this {what} round (round {session.phase.round}).",
                  f"Is {what_hi} round mein {left} bacha hai.", "थोड़ा सा समय बचा है।",
                  emotion="curious")  # fmt: skip
    minutes = 0.0
    m = re.search(r"\d+(?:\.\d+)?", call.arg("amount") or call.said or "")
    if m:
        minutes = float(m.group(0))
        if re.search(r"\b(hour|hours|ghanta|ghante)\b", (call.said or "").lower()):
            minutes *= 60
    minutes = min(max(minutes, 1), 180) if minutes else 0
    session.start(minutes or None)
    work = int(session.work_min)
    return _r(lang, f"Focus mode on: {work} minutes of work, then a {int(session.break_min)} "
              "minute break. I'll guard your taskbar! *serious cat face*",
              f"Focus mode on: {work} minute kaam, phir {int(session.break_min)} minute break. "
              "Main taskbar guard karungi!", "फोकस मोड ऑन! मैं टास्कबार की रखवाली करूँगी।",
              focus="start")  # fmt: skip


# --- phrases
def _start(m: re.Match, said: str) -> dict[str, str]:
    num = re.search(r"\b(\d+)\s*(?:min|mins|minutes|minute|hour|hours|ghante|ghanta)?\b", said)
    return {"target": "start", "amount": num.group(1) if num else ""}


intent(
    "focus",
    r"\b(?:stop|end|exit|cancel|quit|finish|turn off|disable)\s+(?:the\s+)?"
    r"(?:focus|pomodoro|focus mode|focus session)\b",
    r"\b(?:focus|pomodoro|focus mode)\s+(?:off|band|khatam|stop)\b",
    args=lambda m, s: {"target": "stop"},
    priority=8,
)
intent(
    "focus",
    r"\b(?:how much|how long|kitna|kitni)\b.{0,25}\b(?:focus|pomodoro|break)\b",
    r"\b(?:focus|pomodoro|break)\b.{0,20}\b(?:left|remaining|bacha|baki)\b",
    args=lambda m, s: {"target": "status"},
    questions=True,
    priority=8,
)
intent(
    "focus",
    r"\b(?:start|begin|turn on|enable|activate)\s+(?:a\s+|the\s+)?(?:focus|pomodoro)",
    r"^(?:focus|pomodoro)(?:\s+mode)?(?:\s+(?:on|please|now|start))?$",
    r"\b(?:focus|pomodoro|padhai)(?:\s+mode)?\s+(?:on|chalu|start)\s*(?:karo|kar do|do)?\b",
    r"^(?:let'?s |i want to |i need to )?(?:focus|concentrate|study)\s+for\s+\d+",
    r"^focus\s+for\s+",
    r"\bpadhai (?:shuru|start) (?:karte|karo|karna)\b",
    args=_start,
    priority=8,
)

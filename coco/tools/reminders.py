"""Reminders and timers."""

from __future__ import annotations

import re
import time
from datetime import datetime

from coco.tools.intents import clean, intent
from coco.tools.registry import ToolCall, ToolResult, tool
from coco.tools.store import Reminder, Store
from coco.tools.when import parse_duration, parse_when, say_duration, say_time

# trigger words around the reminder's own text
_LEAD = re.compile(
    r"^(?:(?:please|pls|can you|could you|hey)\s+)*(?:set (?:a |an )?reminder|add (?:a )?"
    r"reminder|create (?:a )?reminder|remind me|remind|reminder|mujhe|yaad (?:dilana|dila do|"
    r"dilao|dila dena|dila|karana|karwana)|reminder (?:laga|set) (?:do|karo|kar do))\b\s*"
)
_JOIN = re.compile(r"^(?:to|that|about|for|of|ki|ke|ko|ka|is|:)\s+")
_TAIL = re.compile(
    r"\s+(?:yaad (?:dilana|dila do|dilao|dila dena|dila|karana)|please|pls|na|"
    r"reminder (?:laga|set) (?:do|karo|kar do))$"
)


def reminder_text(said: str, span: tuple[int, int] | None) -> str:
    """The thing to be reminded of, without the time and the "remind me" words."""
    text = said
    if span:
        text = (said[: span[0]] + " " + said[span[1] :]).strip()
    text = re.sub(r"\s+", " ", text)
    for _ in range(3):  # "remind me to", "yaad dilana ki", ...
        new = _JOIN.sub("", _LEAD.sub("", text)).strip()
        new = _TAIL.sub("", new).strip()
        if new == text:
            break
        text = new
    return text.strip(" ,.")


def best_reminder(items: list[Reminder], query: str, now: datetime) -> Reminder | None:
    if not items:
        return None
    q = query.lower()
    if re.search(r"\btimer\b", q):
        timers = [r for r in items if r.kind == "timer"]
        return timers[0] if timers else None
    hit = parse_when(clean(q), now)
    if hit is not None:  # "the 6 o'clock one"
        target = hit[0].timestamp()
        return min(items, key=lambda r: abs(r.due - target))
    words = {w for w in re.findall(r"[a-z0-9]+", q) if len(w) > 2} - {
        "the", "reminder", "reminders", "cancel", "delete", "remove", "wala", "vala", "my", "one",
    }  # fmt: skip
    scored = [(len(words & set(re.findall(r"[a-z0-9]+", r.text.lower()))), r) for r in items]
    score, best = max(scored, key=lambda t: t[0])
    if score:
        return best
    return items[0] if len(items) == 1 or not words else None


class ReminderBook:
    def __init__(self, store: Store, clock=time.time) -> None:
        self.store = store
        self.clock = clock

    def add(self, text: str, due: datetime, kind: str = "reminder", lang: str = "en") -> Reminder:
        return self.store.add_reminder(text, due.timestamp(), kind, lang)

    def pending(self) -> list[Reminder]:
        return self.store.reminders()

    def next_due(self) -> float | None:
        return self.store.next_due()

    def take_due(self) -> list[Reminder]:
        """Reminders that are due now (each handed out once until answered)."""
        due = self.store.due_reminders(self.clock())
        for r in due:
            self.store.mark_shown(r.id)
        return due

    def done(self, reminder_id: int) -> None:
        self.store.finish_reminder(reminder_id)

    def snooze(self, reminder_id: int, minutes: float = 10) -> None:
        self.store.snooze(reminder_id, self.clock() + minutes * 60)

    def cancel(self, reminder: Reminder) -> None:
        self.store.delete_reminder(reminder.id)

    def clear(self) -> int:
        return self.store.delete_all_reminders()


# --- the tool
def _r(lang, en, hinglish, devanagari="", ok=True, emotion="happy", **data) -> ToolResult:
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion, data=data)
    return ToolResult(ok=ok, say=en, emotion=emotion, data=data)


def _confirm(call: ToolCall) -> str:
    if call.lang == "hinglish":
        return "Saare reminders aur timers cancel kar doon? Pakka?"
    return "Cancel ALL your reminders and timers? Are you sure?"


@tool(
    "reminder",
    "reminders and timers (target: add | timer | list | cancel | clear; when: the time as "
    "they said it; text: what to remind them of, or which one to cancel)",
    words=r"remind|reminder|timer|yaad|alarm|wake me|notify|cancel|minutes?|baje|ghante",
    label="Reminders and timers",
    confirm_if=lambda c: c.arg("target") == "clear",
    confirm_text=_confirm,
)
def reminder(call: ToolCall, ctx) -> ToolResult:
    book: ReminderBook = ctx.reminders
    lang = call.lang
    now = ctx.now()
    action = call.arg("target", "add").lower()
    said = clean(call.said or "")
    if action == "list":
        items = book.pending()
        if not items:
            return _r(lang, "No reminders right now~", "Abhi koi reminder nahi hai~",
                      "अभी कोई रिमाइंडर नहीं है।", emotion="neutral")  # fmt: skip
        parts = [f"{say_time(datetime.fromtimestamp(r.due), now, lang)}: {r.text}" for r in items]
        return _r(lang, f"You have {len(items)}: " + "; ".join(parts),
                  f"{len(items)} reminders hain: " + "; ".join(parts),
                  f"{len(items)} रिमाइंडर हैं।", emotion="curious")  # fmt: skip
    if action == "clear":
        n = book.clear()
        return _r(lang, f"Cancelled all {n}.", f"Saare {n} cancel kar diye.",
                  "सारे कैंसल कर दिए।", emotion="neutral", changed=True)  # fmt: skip
    if action == "cancel":
        target = best_reminder(book.pending(), call.arg("text") or said, now)
        if target is None:
            return _r(lang, "I couldn't find that reminder. Meow?",
                      "Wo reminder nahi mila. Meow?", "वो रिमाइंडर नहीं मिला।", ok=False,
                      emotion="curious")  # fmt: skip
        book.cancel(target)
        return _r(lang, f"Cancelled: {target.text}.", f"Cancel kar diya: {target.text}.",
                  "कैंसल कर दिया।", emotion="neutral", changed=True)  # fmt: skip

    # ---- add a reminder / timer
    when_text = clean(call.arg("when")) if call.arg("when") else said
    hit = parse_when(when_text, now)
    if hit is None and when_text != said:
        hit = parse_when(said, now)
    if hit is None:
        return _r(lang, "When should I remind you? (like 'at 6' or 'in 20 minutes')",
                  "Kab yaad dilaun? (jaise '6 baje' ya '20 minute baad')",
                  "कब याद दिलाऊँ?", ok=False, emotion="curious")  # fmt: skip
    due, span = hit
    if action == "timer" or (re.search(r"\btimer\b", said) and not call.arg("text")):
        seconds = max(1.0, (due - now).total_seconds())
        rel = parse_duration(when_text)
        label = say_duration(rel[0] if rel else seconds, lang)
        book.add(f"{label} timer", due, "timer", lang)
        return _r(lang, f"Timer set: {label}. I'll tell you when it's done! *tail flick*",
                  f"Timer laga diya: {label}. Ho jaane pe bataungi!",
                  "टाइमर लगा दिया!", changed=True)  # fmt: skip
    text = call.arg("text") or reminder_text(said, span if when_text == said else None)
    text = text or ("your reminder" if lang == "en" else "tumhara reminder")
    book.add(text, due, "reminder", lang)
    at = say_time(due, now, lang)
    return _r(lang, f"Okay! I'll remind you {at}: {text}. *writes on a sticky note*",
              f"Theek hai! {at} yaad dila dungi: {text}.",
              "ठीक है, याद दिला दूँगी!", changed=True)  # fmt: skip


# --- phrases
def _add(m: re.Match, said: str) -> dict[str, str]:
    return {"target": "add"}


intent(
    "reminder",
    r"^(?:cancel|delete|remove|clear|stop)\s+(?:all\s+)?(?:of\s+)?(?:my\s+|the\s+)?"
    r"(?:reminders|timers|reminders and timers)$",
    r"^(?:saare|sab|sabhi) (?:reminders?|timers?) (?:cancel|hata|delete|band) (?:karo|kar do|do)$",
    args=lambda m, s: {"target": "clear"},
    priority=7,
)
intent(
    "reminder",
    r"^(?:cancel|delete|remove|stop)\s+(?:the\s+|my\s+)?(?P<text>.*?)\s*(?:reminder|timer)$",
    r"^(?P<text>.*?)\s*(?:reminder|timer)\s+(?:cancel|hata|band|delete)\s*(?:karo|kar do|do)?$",
    args=lambda m, s: {"target": "cancel", "text": (m.group("text") or "").strip()},
    priority=6,
)
intent(
    "reminder",
    r"\b(?:what|which|show|list|any|read)\b.{0,20}\breminders?\b",
    r"\breminders? (?:dikhao|batao|kya hai|kya hain|sunao)\b",
    r"^(?:my )?reminders\??$",
    args=lambda m, s: {"target": "list"},
    questions=True,
    priority=6,
)
intent(
    "reminder",
    r"\b(?:set|start|add|make|put)\s+(?:a\s+|an\s+)?(?:.{0,25}\s)?timer\b",
    r"\btimer\s+(?:for|of)\b",
    r"\btimer\s+(?:laga|set|start|chalu)\s*(?:do|karo|kar do)?\b",
    r"\b(?:ka|ke)\s+timer\b",
    args=lambda m, s: {"target": "timer"},
    priority=5,
)
intent(
    "reminder",
    r"\bremind me\b",
    r"\b(?:set|add|create|make)\s+(?:a\s+|an\s+)?reminder\b",
    r"\byaad (?:dila|dilana|dilao|dila do|dila dena|karana|karwana)\b",
    r"\breminder (?:laga|set) (?:do|karo|kar do)\b",
    r"\bwake me up\b",
    r"\b(?:nudge|ping|alert|buzz|poke) me\b.{0,40}\b(?:in|at|after|tomorrow|tonight)\b",
    args=_add,
    priority=5,
)

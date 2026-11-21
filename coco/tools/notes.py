"""Notes and to-dos."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool
from coco.tools.store import Note, Store

log = logging.getLogger(__name__)

_STOP = {
    "the", "a", "an", "my", "one", "note", "notes", "to", "do", "list", "item", "task",
    "wala", "vala", "wali", "about", "for", "of", "that", "this", "is", "ka", "ki", "ke",
}  # fmt: skip
_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "last": -1,
             "pehla": 0, "pehli": 0, "dusra": 1, "dusri": 1, "teesra": 2, "aakhri": -1}  # fmt: skip


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1}


def best_match(notes: list[Note], query: str) -> Note | None:
    """Which note does "the ESP32 one" / "the second one" / "number 2" mean?"""
    if not notes:
        return None
    q = query.lower().strip()
    num = re.search(r"\b(?:number|no|#)\s*(\d+)\b|^(\d+)$", q)
    if num:
        i = int(num.group(1) or num.group(2)) - 1
        return notes[i] if 0 <= i < len(notes) else None
    for word, i in _ORDINALS.items():
        if re.search(rf"\b{word}\b", q):
            return notes[i] if -len(notes) <= i < len(notes) else None
    qw = _words(q)
    if not qw:
        return notes[-1] if q in ("", "it", "that", "this", "isko", "usko") else None
    scored = [(len(qw & _words(n.text)), -n.id, n) for n in notes]
    score, _, note = max(scored, key=lambda t: (t[0], t[1]))
    return note if score > 0 else None


class NotesBook:
    def __init__(self, store: Store, md_path: Path | None) -> None:
        self.store = store
        self.md_path = md_path

    def add(self, text: str) -> Note:
        note = self.store.add_note(text.strip().rstrip("."))
        self.write_md()
        return note

    def open_notes(self) -> list[Note]:
        return self.store.notes()

    def done(self, note: Note) -> None:
        self.store.set_note_done(note.id)
        self.write_md()

    def delete(self, note: Note) -> None:
        self.store.delete_note(note.id)
        self.write_md()

    def delete_all(self) -> int:
        n = self.store.delete_all_notes()
        self.write_md()
        return n

    def write_md(self) -> None:
        """notes.md: open ones first, then the last 20 done ones."""
        if self.md_path is None:
            return
        notes = self.store.notes(include_done=True)
        lines = ["# Coco's notes", "", "*(Coco keeps this file up to date: tell her to add, "
                 "tick off or delete notes.)*", ""]  # fmt: skip
        for n in notes:
            if not n.done:
                when = datetime.fromtimestamp(n.created).strftime("%d %b")
                lines.append(f"- [ ] {n.text}  _(added {when})_")
        done = [n for n in notes if n.done][-20:]
        if done:
            lines += ["", "## Done", ""] + [f"- [x] {n.text}" for n in done]
        try:
            self.md_path.parent.mkdir(parents=True, exist_ok=True)
            self.md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            log.warning("Could not write %s", self.md_path, exc_info=True)


def notes_md_path() -> Path:
    from coco.desktop.windows import local_folder

    return local_folder("Documents") / "Coco" / "notes.md"


# --- the tool
def _r(lang: str, en: str, hinglish: str, devanagari: str = "", ok=True, emotion="happy"):
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion)
    return ToolResult(ok=ok, say=en, emotion=emotion)


def _confirm_text(call: ToolCall) -> str:
    what = call.arg("text")
    every = what.lower() in ("all", "everything", "sab", "saare", "sabhi")
    if call.lang == "hinglish":
        return (
            "Saare notes delete kar doon? Pakka?" if every else f"'{what}' wala note delete karun?"
        )
    return "Delete ALL your notes? Are you sure?" if every else f"Delete the note '{what}'?"


@tool(
    "notes",
    "their notes / to-do list (target: add | list | done | delete; text: the note to add, "
    "or which note)",
    words=r"note|list|to ?do|write|jot|likh|remember|yaad rakh|done|delete|remove|tick",
    label="Notes and to-dos",
    confirm_if=lambda c: c.arg("target") == "delete",
    confirm_text=_confirm_text,
)
def notes(call: ToolCall, ctx) -> ToolResult:
    book: NotesBook = ctx.notes
    lang = call.lang
    action = call.arg("target", "add").lower()
    text = call.arg("text")
    if action == "add":
        if not text:
            return _r(lang, "What should I write down? Meow?", "Kya likhun? Meow?", "क्या लिखूँ?")
        book.add(text)
        n = len(book.open_notes())
        return _r(lang, f"Noted: {text}. *scribbles* (That's {n} on your list.)",
                  f"Likh liya: {text}. *scribbles* (List mein ab {n} hain.)",
                  "लिख लिया!")  # fmt: skip
    notes_now = book.open_notes()
    if action == "list":
        if not notes_now:
            return _r(
                lang,
                "Your list is empty! Nothing to do~ *happy tail*",
                "Tumhari list khaali hai! Kuch nahi karna~",
                "तुम्हारी लिस्ट खाली है!",
            )
        items = "; ".join(f"{i}. {n.text}" for i, n in enumerate(notes_now, 1))
        return _r(lang, f"You have {len(notes_now)}: {items}",
                  f"List mein {len(notes_now)} hain: {items}",
                  f"लिस्ट में {len(notes_now)} चीज़ें हैं।", emotion="curious")  # fmt: skip
    if action == "delete" and text.lower() in ("all", "everything", "sab", "saare", "sabhi"):
        n = book.delete_all()
        return _r(lang, f"Done, I deleted all {n} notes.", f"Ho gaya, saare {n} notes delete.",
                  "सारे नोट्स डिलीट कर दिए।", emotion="neutral")  # fmt: skip
    note = best_match(notes_now, text)
    if note is None:
        return _r(lang, f"I couldn't find a note like '{text}'. Meow?",
                  f"'{text}' jaisa koi note nahi mila. Meow?", "ऐसा कोई नोट नहीं मिला।",
                  ok=False, emotion="curious")  # fmt: skip
    if action == "done":
        book.done(note)
        return _r(lang, f"Ticked off: {note.text}. Good job! *purrs*",
                  f"Ho gaya tick: {note.text}. Shabaash! *purrs*", "शाबाश!")  # fmt: skip
    if action == "delete":
        book.delete(note)
        return _r(lang, f"Deleted: {note.text}.", f"Delete kar diya: {note.text}.",
                  "डिलीट कर दिया।", emotion="neutral")  # fmt: skip
    return _r(lang, "Hmm? Meow?", "Hmm? Meow?", ok=False, emotion="curious")


# --- phrases
def _add(m: re.Match, said: str) -> dict[str, str] | None:
    text = m.group("text").strip(" :.,")
    return {"target": "add", "text": text} if text else None


def _which(action: str):
    def build(m: re.Match, said: str) -> dict[str, str]:
        return {"target": action, "text": (m.groupdict().get("text") or "").strip(" :.,")}

    return build


def _all(action: str):
    return lambda m, said: {"target": action, "text": "all"}


intent(
    "notes",
    # Hinglish first: "note kar lo X" must not become the English "note <kar lo X>"
    r"^(?:note kar lo|note karo|note kar do|likh lo|likh do|ye likh lo)\s*(?:ki\s+)?(?P<text>.+)$",
    r"^(?P<text>.+?)\s+(?:note kar lo|note karo|note kar do|likh lo|likh do|"
    r"list (?:mein|me) (?:daal|dal|likh) do)$",
    r"^(?:note down|note|make a note|write down|jot down|add a note|take a note|add note)\b"
    r"\s*(?:that\s+|of\s+|to\s+)?(?P<text>.+)$",
    r"^add\s+(?P<text>.+?)\s+to\s+(?:my\s+|the\s+)?(?:list|to ?do(?: list)?|notes)$",
    r"^remember to\s+(?P<text>.+)$",
    args=_add,
    priority=4,
)
intent(
    "notes",
    r"^(?:delete|clear|remove|erase)\s+(?:all\s+)?(?:of\s+)?(?:my\s+|the\s+)?"
    r"(?:notes|list|to ?dos?|to ?do list)$",
    r"^(?:saare|sab|sabhi) notes? (?:delete|hata|mita) (?:karo|kar do|do)$",
    args=_all("delete"),
    priority=6,
)
intent(
    "notes",
    r"^(?:delete|remove|erase|cross off|scratch)\s+(?:the\s+)?(?P<text>.+?)\s+"
    r"(?:note|from (?:my|the) (?:list|notes|to ?do list))$",
    r"^(?P<text>.+?)\s+(?:wala|vala)?\s*note\s+(?:delete|hata|mita)\s*(?:karo|kar do|do)?$",
    args=_which("delete"),
    priority=4,
)
intent(
    "notes",
    r"^(?:mark|tick|check)\s+(?:off\s+)?(?:the\s+)?(?P<text>.+?)\s+(?:as\s+)?"
    r"(?:done|complete|completed|finished)$",
    r"^(?:tick off|cross off|check off)\s+(?:the\s+)?(?P<text>.+)$",
    r"^(?:i'?m |i am )?done with\s+(?:the\s+)?(?P<text>.+)$",
    r"^(?P<text>.+?)\s+(?:wala|vala|wali)\s+(?:done|ho gaya|ho gayi|kar liya|kar li|complete)$",
    args=_which("done"),
    priority=4,
)
intent(
    "notes",
    r"\b(?:what'?s|what is|whats|show|read|tell me|list|open)\b.{0,20}\b(?:my )?"
    r"(?:list|notes|to ?dos?|to ?do list)\b",
    r"\bmeri (?:list|notes?)\b.{0,15}\b(?:kya|dikhao|batao|padho)\b",
    r"^(?:notes|list|to ?do list) (?:dikhao|batao|padho|sunao)$",
    r"^(?:my )?(?:notes|to ?do list|to ?dos)\??$",
    args=lambda m, said: {"target": "list"},
    questions=True,
    priority=4,
)

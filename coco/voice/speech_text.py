"""Turns a chat reply into the text she speaks."""

from __future__ import annotations

import re

_CODE_BLOCK = re.compile(r"```.*?(```|$)", re.S)
_ACTION = re.compile(r"\*[^*\n]{1,60}\*")  # *yawns*, *slow blink*
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff‍️]+")
_SENTENCE_END = re.compile(r"(?<=[.!?…।])\s+")


def speakable(text: str) -> str:
    """Markdown chat text -> plain words to speak."""
    text = _CODE_BLOCK.sub(" Here's the code. ", text)
    text = _ACTION.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _EMOJI.sub(" ", text)
    text = text.replace("`", "")
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, flags=re.M)  # list bullets
    text = re.sub(r"[#>_~|]", " ", text)
    text = text.replace("*", " ")
    return re.sub(r"\s+", " ", text).strip()


# Where speech can pause: a sentence end, or (in a long sentence) a comma-like break.
_BREAK = re.compile(r"(?P<end>[.!?…।]+)\s+|(?P<clause>[,;:—–])\s+")
CLAUSE_MIN = 40  # only cut at a comma once the piece is at least this long


def _cut(text: str, final: bool) -> tuple[list[str], int]:
    """Split `text` into speakable pieces. Returns (pieces, characters consumed).

    Sentences always end a piece. Long sentences are also cut at commas, so the first
    words can be synthesized (and heard) quickly. Unless `final`, the unfinished tail is
    kept back because more words may still arrive.
    """
    pieces: list[str] = []
    start = 0
    for m in _BREAK.finditer(text):
        piece = text[start : m.end()].strip()
        if m.group("end") or len(piece) >= CLAUSE_MIN:
            if piece:
                pieces.append(piece)
            start = m.end()
    tail = text[start:]
    if final:
        if tail.strip():
            pieces.append(tail.strip())
        return pieces, len(text)
    if re.search(r"[.!?…।]$", tail.rstrip()) and tail.strip():
        pieces.append(tail.strip())  # ends a sentence already, nothing will follow it
        return pieces, len(text)
    return pieces, start


def split_sentences(text: str) -> list[str]:
    """Pieces to speak for a complete text (sentences, long ones cut at commas)."""
    return _cut(text, final=True)[0]


class SentenceStream:
    """Feed it the growing reply text; it hands back pieces once they're complete.

    feed("Hi! How a") -> ["Hi!"]      feed("Hi! How are you?", final=True) -> ["How are you?"]
    """

    def __init__(self, max_chars: int = 600) -> None:
        self.max_chars = max_chars  # stop reading very long answers out loud
        self.spoken_upto = 0  # characters of the *speakable* text already handed out
        self.total = 0

    def feed(self, text_so_far: str, final: bool = False) -> list[str]:
        clean = speakable(text_so_far)
        pending = clean[self.spoken_upto :]
        if not pending.strip():
            return []
        pieces, used = _cut(pending, final)
        self.spoken_upto += used
        out = []
        for p in pieces:
            if self.total >= self.max_chars:
                break
            out.append(p)
            self.total += len(p)
        return out

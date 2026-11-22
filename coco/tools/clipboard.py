"""Read and write the clipboard (text only, on request)."""

from __future__ import annotations

import logging
import re

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool

log = logging.getLogger(__name__)

MAX_CHARS = 4000


class Clipboard:
    """Windows clipboard text (pywin32; works from any thread)."""

    def read(self) -> str:
        import win32clipboard as cb

        cb.OpenClipboard()
        try:
            if cb.IsClipboardFormatAvailable(cb.CF_UNICODETEXT):
                return str(cb.GetClipboardData(cb.CF_UNICODETEXT) or "")
            return ""
        finally:
            cb.CloseClipboard()

    def write(self, text: str) -> None:
        import win32clipboard as cb

        cb.OpenClipboard()
        try:
            cb.EmptyClipboard()
            cb.SetClipboardData(cb.CF_UNICODETEXT, text)
        finally:
            cb.CloseClipboard()


def looks_secret(text: str) -> bool:
    """One 'word' that looks like a password / key / OTP: don't read it out."""
    t = text.strip()
    if not t or " " in t or "\n" in t:
        return False
    if re.fullmatch(r"\d{4,8}", t):  # an OTP / PIN
        return True
    kinds = sum(bool(re.search(p, t)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w]"))
    return 8 <= len(t) <= 128 and kinds >= 3 and not re.match(r"https?://", t)


def clean_reply(text: str) -> str:
    """Her reply without *actions* and cat sounds, ready to paste."""
    t = re.sub(r"\*[^*\n]{1,60}\*", "", text)
    t = re.sub(r"\b(?:meow|mrrp)\b[~!.]*", "", t, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", t).strip(" ~")


def _r(lang, en, hinglish, devanagari="", ok=True, emotion="happy") -> ToolResult:
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion)
    return ToolResult(ok=ok, say=en, emotion=emotion)


@tool(
    "clipboard",
    "the clipboard (target: read = read what they copied to answer their question about it; "
    "copy = copy your last answer)",
    words=r"copied|copy|clipboard|paste|pasted",
    label="Clipboard",
)
def clipboard(call: ToolCall, ctx) -> ToolResult:
    lang = call.lang
    clip = ctx.clipboard
    if call.arg("target", "read").lower() == "copy":
        answer = clean_reply(ctx.last_answer() or "")
        if not answer:
            return _r(lang, "I haven't said anything to copy yet~",
                      "Abhi copy karne ko kuch nahi hai~",
                      "कुछ नहीं है।", ok=False, emotion="curious")  # fmt: skip
        clip.write(answer)
        return _r(lang, "Copied! Paste it anywhere~ 📋", "Copy kar diya! Kahin bhi paste karo~ 📋",
                  "कॉपी कर दिया!")  # fmt: skip
    try:
        text = clip.read()
    except Exception:  # noqa: BLE001 - another app holds the clipboard
        log.exception("clipboard read failed")
        text = None
    if text is None:
        return _r(lang, "The clipboard is busy, try again?", "Clipboard busy hai, phir try karo?",
                  "फिर से कोशिश करो।", ok=False, emotion="sad")  # fmt: skip
    if not text.strip():
        return _r(lang, "Your clipboard is empty (or it's a picture: I only read text)~",
                  "Clipboard khaali hai (ya photo hai, main sirf text padhti hoon)~",
                  "क्लिपबोर्ड खाली है।", ok=False, emotion="curious")  # fmt: skip
    if looks_secret(text):
        return _r(lang, "That looks like a password or a code, so I won't read it out~ 🔒",
                  "Ye password ya code lag raha hai, main nahi padhungi~ 🔒",
                  "ये पासवर्ड लग रहा है।", ok=False, emotion="neutral")  # fmt: skip
    cut = text[:MAX_CHARS]
    more = f" (first {MAX_CHARS} of {len(text)} characters)" if len(text) > MAX_CHARS else ""
    return ToolResult(ok=True, facts=f"The text they copied{more}:\n<<<\n{cut}\n>>>",
                      emotion="curious")  # fmt: skip


# --- phrases
intent(
    "clipboard",
    r"\b(?:what i|what i've|what i have|the text i|the thing i)\s+(?:just\s+)?copied\b|"
    r"\b(?:my|the)\s+clipboard\b|\bcopied\s+(?:text|code|thing|message|error)\b|"
    r"\bcopy\s+kiya\s+(?:hua|hai)\b|\bclipboard\s+(?:mein|me|pe)\b",
    args=lambda m, s: {"target": "read"},
    questions=True,
    priority=7,
)
intent(
    "clipboard",
    r"^(?:please\s+|coco,?\s+)?copy\s+(?:that|this|it|your\s+(?:answer|reply|last answer)|"
    r"what you (?:said|wrote))(?:\s+(?:for me|please))?$|"
    r"^(?:ye|yeh|isko|isse|apna jawab)\s+copy\s+(?:karo|kar do|kardo)$",
    args=lambda m, s: {"target": "copy"},
    priority=7,
)

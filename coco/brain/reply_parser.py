"""Parses Coco's JSON reply while it streams in."""

from __future__ import annotations

import json

from coco.behaviour.commands import ACTIONS
from coco.brain.schemas import EMOTIONS

_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
            "t": "\t"}  # fmt: skip


def normalize_emotion(value: object) -> str:
    """Map whatever the model said to one of EMOTIONS (unknown -> neutral)."""
    word = str(value or "").strip().lower()
    return word if word in EMOTIONS else "neutral"


def normalize_action(value: object) -> str:
    """Map the model's action to one of ACTIONS (anything else -> "none")."""
    word = str(value or "").strip().lower().replace(" ", "_")
    return word if word in ACTIONS else "none"


def _read_string(raw: str, i: int) -> tuple[str, int, bool]:
    """Decode a JSON string starting just after its opening quote at raw[i].

    Returns (decoded_so_far, index_after_string, complete). Stops cleanly in the middle
    of an escape sequence that hasn't fully arrived yet.
    """
    out: list[str] = []
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == '"':
            return "".join(out), i + 1, True
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            break  # backslash is the last character so far: wait for more
        e = raw[i + 1]
        if e in _ESCAPES:
            out.append(_ESCAPES[e])
            i += 2
        elif e == "u":
            if i + 6 > n:
                break  # \\uXXXX not complete yet
            code = int(raw[i + 2 : i + 6], 16)
            i += 6
            # surrogate pair (emoji): \\ud83d\\ude3a
            if 0xD800 <= code < 0xDC00:
                if i + 6 > n:
                    i -= 6
                    break
                if raw[i : i + 2] == "\\u":
                    low = int(raw[i + 2 : i + 6], 16)
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    i += 6
            out.append(chr(code))
        else:  # invalid escape: keep it literally
            out.append(e)
            i += 2
    return "".join(out), i, False


def scan_partial(raw: str) -> dict[str, tuple[str, bool]]:
    """Top-level string fields of a (possibly unfinished) JSON object.

    Returns {key: (value_so_far, complete)}. Non-string values are skipped.
    """
    fields: dict[str, tuple[str, bool]] = {}
    i, n = 0, len(raw)
    text = raw
    # tolerate a ```json fence some models add
    stripped = text.lstrip()
    if stripped.startswith("```"):
        nl = stripped.find("\n")
        text = stripped[nl + 1 :] if nl >= 0 else ""
        n = len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "{":
        return fields
    i += 1
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] == "}":
            break
        if text[i] != '"':
            return fields  # not JSON we understand
        key, i, ok = _read_string(text, i + 1)
        if not ok:
            break
        while i < n and text[i] in " \t\r\n:":
            i += 1
        if i >= n:
            break
        if text[i] == '"':
            value, i, complete = _read_string(text, i + 1)
            fields[key] = (value, complete)
            if not complete:
                break
        else:  # number / bool / null / nested: skip to the next comma or brace
            depth = 0
            while i < n:
                c = text[i]
                if c in "{[":
                    depth += 1
                elif c in "}]":
                    if depth == 0:
                        break
                    depth -= 1
                elif c == "," and depth == 0:
                    break
                i += 1
    return fields


class ReplyParser:
    def __init__(self) -> None:
        self.raw = ""
        self.text = ""
        self.emotion: str | None = None  # None until the model has committed to one
        self.action = "none"  # something to do, once its string is complete
        self.speech_hi = ""  # Hinglish replies: the same in Devanagari, for her voice
        self.tool = "none"  # a PC task, once its string is complete
        self.tool_args: dict[str, str] = {}
        self.remember = ""  # a fact about them worth keeping ("" = none)

    def feed(self, chunk: str) -> bool:
        """Add a chunk. Returns True if `text`, `speech_hi` or `emotion` changed."""
        if not chunk:
            return False
        self.raw += chunk
        fields = scan_partial(self.raw)
        changed = False
        if "text" in fields and fields["text"][0] != self.text:
            self.text = fields["text"][0]
            changed = True
        if "speech_hi" in fields and fields["speech_hi"][0] != self.speech_hi:
            self.speech_hi = fields["speech_hi"][0]
            changed = True
        if self.emotion is None and "emotion" in fields and fields["emotion"][1]:
            self.emotion = normalize_emotion(fields["emotion"][0])
            changed = True
        if "action" in fields and fields["action"][1]:
            self.action = normalize_action(fields["action"][0])
        if "tool" in fields and fields["tool"][1]:
            self.tool = str(fields["tool"][0]).strip() or "none"
        return changed

    def finish(self) -> tuple[str, str]:
        """Final (text, emotion) once the stream has ended."""
        raw = self.raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("{") :] if "{" in raw else raw
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("text"), str):
                self.action = normalize_action(data.get("action"))
                speech = data.get("speech_hi")
                self.speech_hi = speech.strip() if isinstance(speech, str) else ""
                tool = data.get("tool")
                self.tool = tool.strip() if isinstance(tool, str) and tool.strip() else "none"
                keep = data.get("remember")
                self.remember = keep.strip() if isinstance(keep, str) else ""
                args = data.get("args")
                self.tool_args = (
                    {str(k): str(v).strip() for k, v in args.items() if str(v).strip()}
                    if isinstance(args, dict)
                    else {}
                )
                return data["text"].strip(), normalize_emotion(data.get("emotion"))
        except ValueError:
            pass
        if self.text:  # cut off mid-way: use what we decoded
            return self.text.strip(), self.emotion or "neutral"
        if raw and not raw.startswith("{"):  # model ignored the format: plain text
            return raw, "neutral"
        return "", self.emotion or "neutral"

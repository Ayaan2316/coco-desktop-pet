"""Strips the owner's name from most replies so she doesn't overuse it."""

from __future__ import annotations

import re


class NameFilter:
    def __init__(self, name: str) -> None:
        self.name = name.strip()
        n = re.escape(self.name)
        flags = re.IGNORECASE
        self._possessive = re.compile(rf"\b{n}'s\b", flags)
        self._after_comma = re.compile(rf"\s*,\s*{n}\b(?!')", flags)  # "Anytime, Sam!"
        # "Sam, that's..." at the start of the text or of a sentence
        self._leading = re.compile(rf"(^|[.!?]\s+|\n)\s*{n}\s*,\s*(\w?)", flags)
        self._greeting = re.compile(rf"\b(hey|hi|hello|oh|yay|aww|okay|ok)\s+{n}\b", flags)
        self._bare = re.compile(rf"\b{n}\b", flags)
        self._seps = re.compile(r"[,\s]*$")
        self._last_word = re.compile(r"[,\s]*(\w+)('s?)?$")

    def mentions(self, text: str) -> bool:
        return bool(self.name) and bool(self._bare.search(text))

    def clean(self, text: str) -> str:
        """Remove the name from finished text."""
        if not self.name:
            return text
        text = self._possessive.sub(lambda m: "Your" if _starts_sentence(text, m) else "your", text)
        text = self._after_comma.sub("", text)
        text = self._leading.sub(lambda m: m.group(1) + m.group(2).upper(), text)
        text = self._greeting.sub(lambda m: m.group(1), text)
        text = self._bare.sub(lambda m: "You" if _starts_sentence(text, m) else "you", text)
        return text

    def view(self, text: str, final: bool) -> str:
        """The text to show so far: cleaned, with a possible half-written name held back."""
        if not self.name:
            return text
        if not final:
            # hold back trailing commas/spaces (a name may follow) ...
            text = text[: self._seps.search(text).start()]
            # ... and a last word that could still turn into the name ("Ay")
            m = self._last_word.search(text)
            if m and self.name.lower().startswith(m.group(1).lower()):
                text = text[: m.start()]
        return self.clean(text)


def _starts_sentence(text: str, m: re.Match) -> bool:
    before = text[: m.start()].rstrip()
    return not before or before[-1] in ".!?\n"

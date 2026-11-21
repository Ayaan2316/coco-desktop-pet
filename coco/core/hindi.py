"""Devanagari helpers: detection and rough transliteration to Hinglish."""

from __future__ import annotations

import re

_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j",
    "झ": "jh", "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t",
    "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh",
    "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s",
    "ह": "h", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
    "य़": "y",
}  # fmt: skip
_NUKTA_FORMS = {"क": "क़", "ख": "ख़", "ग": "ग़", "ज": "ज़", "ड": "ड़", "ढ": "ढ़", "फ": "फ़", "य": "य़"}
_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e",
    "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o",
}  # fmt: skip
_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai",
    "ो": "o", "ौ": "au", "ॉ": "o",
}  # fmt: skip
_NASAL = {"ं": "n", "ँ": "n", "ः": "h"}
_VIRAMA = "्"
_NUKTA = "़"
_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text))


def _word(word: str) -> str:
    out: list[str] = []
    chars = list(word)
    i = 0
    while i < len(chars):
        c = chars[i]
        if i + 1 < len(chars) and chars[i + 1] == _NUKTA and c in _NUKTA_FORMS:
            c = _NUKTA_FORMS[c]
            i += 1
        if c in _CONSONANTS:
            out.append(_CONSONANTS[c])
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if nxt in _MATRAS:
                last = i + 2 >= len(chars) or chars[i + 2] in _NASAL and i + 3 >= len(chars)
                out.append("a" if nxt == "ा" and last else _MATRAS[nxt])
                i += 2
                continue
            if nxt == _VIRAMA:
                i += 2
                continue
            if i + 1 < len(chars):  # the inherent "a" (dropped at the end of a word)
                out.append("a")
            i += 1
            continue
        if c in _VOWELS:
            out.append(_VOWELS[c])
        elif c in _NASAL:
            out.append(_NASAL[c])
        elif c in _MATRAS:  # a stray matra
            out.append(_MATRAS[c])
        else:
            out.append(c)
        i += 1
    return "".join(out)


# Common Hindi words in Hinglish (English letters). Two or more = it's Hinglish.
_HINDI_WORDS = {
    "hai", "hain", "ho", "hoon", "hu", "tha", "thi", "kya", "kyun", "kyon", "kaise", "kaisa",
    "kaisi", "kab", "kahan", "kaun", "kitna", "mera", "meri", "mere", "mujhe", "mujhko",
    "tum", "tumhara", "aap", "aapka", "hum", "humko", "main", "mai", "nahi", "nahin", "na",
    "bhi", "aur", "ya", "ka", "ki", "ke", "ko", "se", "mein", "par", "pe", "raha", "rahi",
    "rahe", "karo", "kar", "karna", "karta", "karti", "do", "dena", "batao", "bata",
    "samjhao", "sunao", "chahiye", "accha", "acha", "theek", "thik", "haan", "yaar", "yar",
    "bahut", "bohot", "thoda", "kuch", "sab", "aaj", "kal", "abhi", "phir", "fir", "toh",
    "to", "wala", "wali", "gaya", "gayi", "jao", "aao", "chalo", "lo", "lena", "dekho",
    "ruko", "bhaag", "khelo", "uth", "idhar", "laga", "lagao", "ek", "kaha",
}  # fmt: skip


_ALSO_ENGLISH = {"to", "do", "main", "mai", "na", "ya", "lo", "par", "pe", "fir", "so"}


def looks_hinglish(text: str) -> bool:
    """Was this written or spoken in Hindi/Hinglish? (Devanagari, or 2+ Hindi words.)"""
    if has_devanagari(text):
        return True
    words = re.findall(r"[a-z]+", text.lower())
    strong = sum(w in _HINDI_WORDS and w not in _ALSO_ENGLISH for w in words)
    if not strong:
        return False  # "how do I do this?" is English, even with "do" twice
    # words that are also English ("to", "do", "main") count half, once it's clearly Hindi
    score = strong + 0.5 * sum(w in _ALSO_ENGLISH for w in words)
    return score >= 1.5 or score >= 0.3 * len(words)


def to_roman(text: str) -> str:
    """Devanagari -> rough Hinglish in English letters (other text is left alone)."""
    if not has_devanagari(text):
        return text
    text = text.translate(_DIGITS).replace("।", ".")
    return re.sub(r"[ऀ-ॿ]+", lambda m: _word(m.group(0)), text)

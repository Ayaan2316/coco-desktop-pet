"""Parses times for reminders and timers, in English and Hinglish."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_NUM = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50,
    "few": 3, "couple of": 2, "a couple of": 2,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
    "che": 6, "chheh": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11,
    "barah": 12, "baarah": 12, "pandrah": 15, "bees": 20, "tees": 30, "chalis": 40,
    "pachas": 50, "half": 0.5, "half an": 0.5, "aadha": 0.5, "aadhe": 0.5, "adha": 0.5,
    "adhe": 0.5, "dedh": 1.5, "dhai": 2.5, "dhaai": 2.5,
}  # fmt: skip
_NUM_RE = "|".join(sorted((re.escape(k) for k in _NUM), key=len, reverse=True))
_HOUR_WORDS = [  # only these can be a clock hour ("at a" / "few baje" are not times)
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
    "twelve", "ek", "do", "teen", "char", "chaar", "paanch", "panch", "chhe", "che", "saat",
    "aath", "nau", "das", "gyarah", "barah", "baarah", "dedh", "dhai", "dhaai",
]  # fmt: skip
_HOUR_RE = "|".join(sorted(_HOUR_WORDS, key=len, reverse=True))
_N = rf"(?P<n>\d+(?:\.\d+)?|{_NUM_RE})"
_UNIT = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "second s": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "minat": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "ghanta": 3600, "ghante": 3600, "ghanton": 3600, "ghante ke": 3600,
}  # fmt: skip
_U = r"(?P<u>seconds?|secs?|minutes?|mins?|minat|hours?|hrs?|ghant(?:a|e|on))"

_RELATIVE = [
    re.compile(rf"\b(?:in|after|within)\s+(?:about\s+)?{_N}\s*{_U}\b"),
    re.compile(rf"\b{_N}\s*{_U}\s*(?:mein|me|main|baad|ke baad|bad|ke bad|later|from now)\b"),
    re.compile(rf"\bfor\s+{_N}\s*{_U}\b"),  # timers: "a timer for 5 minutes"
    re.compile(rf"\b{_N}\s*{_U}\s*(?:ka|ke|ki)?\s*timer\b"),  # "5 minute ka timer"
    re.compile(rf"\btimer\s+(?:of\s+)?{_N}\s*{_U}\b"),
]
_DAY = re.compile(
    r"\b(?P<day>tomorrow|tmrw|tomorow|day after tomorrow|parso|kal|today|aaj|tonight|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"somvar|mangalvar|budhvar|guruvar|shukravar|shanivar|ravivar|itvar)\b"
)
_PART = re.compile(
    r"\b(?P<part>morning|subah|afternoon|dopahar|dophar|evening|shaam|sham|night|raat|"
    r"noon|midnight)\b"
)
_HINDI_FRACTION = {"saade": 30, "sade": 30, "sava": 15, "sawa": 15, "paune": -15, "pone": -15}
_CLOCK = re.compile(
    r"\b(?:at\s+|by\s+|around\s+)?"
    r"(?:(?P<frac>saade|sade|sava|sawa|paune|pone)\s+)?"
    rf"(?P<h>\d{{1,2}}|{_HOUR_RE})(?:[:.](?P<m>\d{{2}}))?"
    r"\s*(?P<ap>am|pm|a m|p m)?"
    r"\s*(?P<baje>baje|bajey|baj|o ?clock|oclock)?\b"
)
_WEEKDAYS = {
    "monday": 0, "somvar": 0, "tuesday": 1, "mangalvar": 1, "wednesday": 2, "budhvar": 2,
    "thursday": 3, "guruvar": 3, "friday": 4, "shukravar": 4, "saturday": 5,
    "shanivar": 5, "sunday": 6, "ravivar": 6, "itvar": 6,
}  # fmt: skip


def _num(word: str) -> float:
    try:
        return float(word)
    except ValueError:
        return float(_NUM[word])


def parse_duration(text: str) -> tuple[float, tuple[int, int]] | None:
    """ "in 20 minutes" / "aadhe ghante mein" -> (seconds, span)."""
    for pattern in _RELATIVE:
        m = pattern.search(text)
        if m:
            unit = re.sub(r"s$", "", m.group("u")) if m.group("u").endswith("s") else m.group("u")
            seconds = _num(m.group("n")) * _UNIT.get(unit, _UNIT.get(m.group("u"), 60))
            if seconds > 0:
                return seconds, m.span()
    return None


def parse_when(text: str, now: datetime) -> tuple[datetime, tuple[int, int]] | None:
    rel = parse_duration(text)
    if rel is not None:
        seconds, span = rel
        return now + timedelta(seconds=seconds), span

    day_m = _DAY.search(text)
    part_m = _PART.search(text)
    clock_m = None
    for m in _CLOCK.finditer(text):
        # a bare number is only a time with a hint: "at 6", "6 baje", "6pm", "6:30", ...
        has_hint = (
            m.group(0).lstrip().startswith(("at", "by", "around"))
            or m.group("baje")
            or m.group("ap")
            or m.group("m")
            or m.group("frac")
        )
        near_day = (day_m or part_m) and (
            abs(m.start() - (day_m or part_m).end()) <= 2
            or abs((day_m or part_m).start() - m.end()) <= 2
        )
        h = _num(m.group("h"))
        if (has_hint or near_day) and 0 <= h <= 24:
            clock_m = m
            break
    part = part_m.group("part") if part_m else ""
    if clock_m is None and part not in ("noon", "midnight") and not (day_m and part):
        return None

    # ---- which day
    day = day_m.group("day") if day_m else ""
    base = now.date()
    later_day = False
    if day in ("tomorrow", "tmrw", "tomorow", "kal"):
        base += timedelta(days=1)
        later_day = True
    elif day in ("day after tomorrow", "parso"):
        base += timedelta(days=2)
        later_day = True
    elif day in _WEEKDAYS:
        ahead = (_WEEKDAYS[day] - now.weekday()) % 7 or 7
        base += timedelta(days=ahead)
        later_day = True

    # ---- which time
    if clock_m is not None:
        value = _num(clock_m.group("h"))  # "dedh" = 1.5 -> 1:30
        hour = int(value)
        minute = int(clock_m.group("m") or 0) + int(round((value - hour) * 60))
        frac = clock_m.group("frac")
        if frac:
            minute += _HINDI_FRACTION[frac]
            if minute < 0:
                hour, minute = hour - 1, minute + 60
        ap = (clock_m.group("ap") or "").replace(" ", "")
    elif part == "noon":
        hour, minute, ap = 12, 0, "pm"
    elif part == "midnight":
        hour, minute, ap = 0, 0, "am"
    else:  # "kal subah" / "tomorrow evening" with no clock
        hour = {"morning": 9, "subah": 9, "afternoon": 14, "dopahar": 14, "dophar": 14,
                "evening": 18, "shaam": 18, "sham": 18, "night": 21, "raat": 21}[part]  # fmt: skip
        minute, ap = 0, "pm" if hour >= 12 else "am"
    if not ap and part:
        ap = "am" if part in ("morning", "subah") else "pm" if part != "midnight" else "am"
        if part in ("night", "raat") and hour in (1, 2, 3, 4):  # "raat 2 baje" = 2 am
            ap = "am"
    if day == "tonight" and not ap:
        ap = "pm"

    if ap == "pm" and hour < 12:
        hour += 12
    elif ap == "am" and hour == 12:
        hour = 0
    if hour >= 24 or minute >= 60:
        return None

    candidates: list[datetime] = []
    when = datetime(base.year, base.month, base.day, hour, minute)
    if ap or hour >= 13 or hour == 0:
        candidates = [when]
    elif later_day:
        # tomorrow at 9 -> 9 am; tomorrow at 5 -> 5 pm; tomorrow at 12 -> noon
        candidates = [when if 7 <= hour <= 12 else when + timedelta(hours=12)]
    else:
        candidates = [when, when + timedelta(hours=12)]  # 6 -> 6:00 or 18:00
    result = next((c for c in sorted(candidates) if c > now), None)
    if result is None:  # all of today's are over: the first one tomorrow
        result = min(candidates) + timedelta(days=1)

    spans = [m.span() for m in (day_m, part_m, clock_m) if m is not None]
    return result, (min(s[0] for s in spans), max(s[1] for s in spans))


def say_time(when: datetime, now: datetime, lang: str = "en") -> str:
    """ "at 6:00 pm" / "tomorrow at 9:00 am" / "kal subah 9:00 baje"."""
    clock12 = when.strftime("%I:%M").lstrip("0")
    day = ""
    if when.date() != now.date():
        delta = (when.date() - now.date()).days
        day = ("tomorrow " if lang == "en" else "kal ") if delta == 1 else when.strftime("%A ")
    if lang == "hinglish":
        h = when.hour
        part = "subah" if 4 <= h < 12 else "dopahar" if h < 16 else "shaam" if h < 20 else "raat"
        return f"{day}{part} {clock12} baje"
    return f"{day}at {clock12} {'am' if when.hour < 12 else 'pm'}"


def say_duration(seconds: float, lang: str = "en") -> str:
    minutes = round(seconds / 60)
    if seconds < 60:
        n = int(seconds)
        return f"{n} seconds" if lang == "en" else f"{n} second"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 and lang == 'en' else ''}"
    hours, rest = divmod(minutes, 60)
    h = f"{hours} hour{'s' if hours != 1 else ''}" if lang == "en" else f"{hours} ghante"
    return h + (f" {rest} min" if rest else "")

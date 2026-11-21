"""Quick commands like "Coco, run!" or "so jao", handled without the AI."""

from __future__ import annotations

import re

from coco.core.hindi import has_devanagari, to_roman

# Every action Coco can be asked to do. The AI's schema uses exactly this list too.
ACTIONS = (
    "run", "walk", "sleep", "get_up", "jump_up", "come_here", "stop", "peek", "play",
    "stretch", "groom", "yawn", "loaf",
)  # fmt: skip

# action -> phrases (regular expressions) that mean it. Matched against the whole
# (normalised) sentence, so "run" matches "run!" but not "my code won't run".
_PATTERNS: dict[str, str] = {
    "peek": r"peek( ?a ?boo)?|(play )?hide( and seek)?",
    "play": r"play( with me)?|hunt|pounce|attack|"
            r"(catch|chase|get)( (it|that|the (mouse|cursor|pointer)))?",
    "run": r"run( around| fast| away)?|zoom(ies)?|go zoom|race|sprint|do (the |some )?zoomies",
    "walk": r"walk( around| about)?|(go for|take) a walk|stroll|wander( around)?|move( around)?",
    "sleep": r"(go )?(to )?(sleep|bed|nap)|(go )?(and )?(take|have) a nap|nap ?time|bed ?time|"
             r"(go )?rest( a bit| for a (bit|while))?|go home|go to your (bed|hammock)|lie down|"
             r"time (for|to) (bed|sleep)",
    "get_up": r"(get|wake) up|wake|rise and shine|get out of (bed|the hammock)|hop out",
    "jump_up": r"jump( up)?( on(to)?( (my|the|this|that))? (window|screen|top|it))?|"
               r"climb( up)?",
    "come_here": r"come( here| over( here)?| to me| back)?|here kitty( kitty)?|over here",
    "stop": r"stop( it| that| moving| running| walking)?|stay( there| put)?|sit( down)?|"
            r"freeze|wait|halt|calm down|be still",
    "stretch": r"(do a |have a )?stretch( out)?",
    "groom": r"groom( yourself)?|clean( yourself)?( up)?|wash( yourself| your face)?|"
             r"lick your paws?",
    "yawn": r"yawn",
    "loaf": r"loaf|(be a|do the) loaf",
}  # fmt: skip
_COMPILED = {a: re.compile(rf"(?:{p})") for a, p in _PATTERNS.items()}

# The same actions in Hinglish ("Coco, so jao"). Devanagari is romanised first (core/hindi),
# and doubled vowels are squeezed before matching (jaao -> jao, bhaag -> bhag, saath ->
# sath), so these patterns are written in that squeezed spelling.
_HINGLISH: dict[str, str] = {
    "peek": r"chh?up( ?ja(o)?|o)?|chh?upan ?chh?upai",
    "play": r"(mere sath )?khel(o|o na)?( mere sath)?",
    "run": r"bhag(o| ja(o)?)?|daud(o)?",
    "walk": r"gh(o|u)m(o)?( lo| ao)?|teh?l(o)?|tahal(o)?",
    "sleep": r"(so|sone) ?ja(o|a)?( na)?|(so|sona) (jao|hai)|neend|nind|aram kar(o| lo)|"
             r"bed (pe|par) ja(o)?",
    "get_up": r"uth(o| ja(o)?)?|jag(o| ja(o)?)?",
    "jump_up": r"kod(o| ja(o)?)?|uchh?al(o)?|chadh( ja(o)?|o)?|"
               r"(window|khidki) (pe|par) (chadh|kod)(o| ja(o)?)?",
    "come_here": r"(idh?ar|ider|yaha(n)?|pas|mere pas) (ao|a ?ja(o)?)",
    "stop": r"ruk(o| ja(o)?)?|bas( kar(o)?)?|tham ja(o)?",
    "stretch": r"angd?r?ai( lo)?",
    "groom": r"(apne aap ko )?saf (kar(o| lo)|ho ja(o)?)",
    "yawn": r"jamhai( lo)?|ubasi( lo)?",
}  # fmt: skip
_COMPILED_HI = {a: re.compile(rf"(?:{p})") for a, p in _HINGLISH.items()}

# Polite / filler words around a command: "hey coco, could you please run now?"
_LEAD = re.compile(
    r"^(?:(?:hey|hi|ok|okay|oi|yo|arre|arey)\s+)?(?:(?:coco|koko|kuku)\s+)?"
    r"(?:(?:please|pls|can you|could you|would you|will you|go and|now|just|quickly|"
    r"zara|jaldi|ab|abhi|thoda|chalo)\s+)*"
)
_TRAIL = re.compile(
    r"(?:\s+(?:please|pls|now|coco|koko|for me|right now|again|kitty|"
    r"na|zara|jaldi|abhi|yaar|yar|karo|kar do|kro|kar))*$"
)
# A real question isn't a command ("why do cats sleep so much?", "kya tum so rahi ho?")
_QUESTION_START = re.compile(
    r"^(?:what|why|how|when|where|who|which|whose|do|does|did|is|are|was|were|"
    r"should|shall|have|has|tell me|kya|kyun|kyon|kaise|kab|kahan|kaun|kitna)\b"
)


# "jump karo", "zara stretch karo na": English verb, Hinglish sentence -> reply in Hinglish
_HINGLISH_FILLERS = {"karo", "kro", "na", "zara", "yaar", "yar", "jaldi", "abhi", "arre", "chalo"}


def normalise(text: str) -> str:
    text = to_roman(text).lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _squeeze(text: str) -> str:
    """Hinglish spellings vary (jaao / jao, bhaag / bhag): squeeze doubled vowels."""
    return re.sub(r"([aeiou])\1+", r"\1", text)


def _match(text: str) -> tuple[str, str] | None:
    """(action, "en" or "hinglish") for a short command, else None."""
    clean = normalise(text)
    if not clean or len(clean.split()) > 9:
        return None  # long sentences are conversations: let the AI handle them
    if _QUESTION_START.match(clean):
        return None
    core = _TRAIL.sub("", _LEAD.sub("", clean)).strip()
    if not core:
        return None
    for action, pattern in _COMPILED.items():
        if pattern.fullmatch(core):
            hinglish = has_devanagari(text) or bool(_HINGLISH_FILLERS & set(clean.split()))
            return action, "hinglish" if hinglish else "en"
    squeezed = _squeeze(core)
    for action, pattern in _COMPILED_HI.items():
        if pattern.fullmatch(squeezed):
            return action, "hinglish"
    return None


def parse_command(text: str) -> str | None:
    """The action a short command asks for, or None if it's not (just) a command."""
    found = _match(text)
    return found[0] if found else None


def command_language(text: str) -> str:
    """ "hinglish" if this command was said in Hinglish/Hindi (her reply should be too)."""
    found = _match(text)
    return found[1] if found else "en"

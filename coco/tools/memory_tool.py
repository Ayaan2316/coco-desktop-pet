"""The memory tool: remember, list and forget facts."""

from __future__ import annotations

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool


def _r(lang, en, hinglish, devanagari="", ok=True, emotion="happy") -> ToolResult:
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion)
    return ToolResult(ok=ok, say=en, emotion=emotion)


@tool(
    "memory",
    "her memory of them (target: add | list | forget; text: the fact to remember, or what "
    "to forget)",
    words=r"remember|forget|yaad|bhool|bhul|memory|memories",
    label="Memory",
)
def memory(call: ToolCall, ctx) -> ToolResult | None:
    book = ctx.memory
    lang = call.lang
    action = call.arg("target", "list").lower()
    text = call.arg("text").strip(" .!?")
    owner = getattr(getattr(ctx, "cfg", None), "owner_name", "") or "They"
    if action == "add":
        if len(text) < 3:
            return _r(lang, "Remember what? Meow?", "Kya yaad rakhun?", "क्या याद रखूँ?",
                      ok=False, emotion="curious")  # fmt: skip
        fact = text if text.lower().startswith(owner.lower()) else f"{owner} said: {text}"
        book.add(fact, source="you", force=True)
        return _r(lang, "Got it, I'll remember that~ 📝 *tail swish*",
                  "Yaad rakh liya~ 📝", "याद रख लिया!")  # fmt: skip
    if action == "forget":
        found = book.find(text) if text else []
        if not found:
            return _r(lang, "I don't remember anything like that~ Meow?",
                      "Aisa kuch yaad hi nahi hai~ Meow?", "ऐसा कुछ याद नहीं है।",
                      ok=False, emotion="curious")  # fmt: skip
        choices = [(str(m.id), m.text[:40]) for m in found] + [("no", "Never mind")]
        question = "Forget which one?" if lang != "hinglish" else "Kaunsi baat bhoolun?"

        def picked(key: str) -> None:
            if not key.isdigit():
                ctx.report(call, _r(lang, "Okay, I'll keep it~", "Theek hai, yaad rakhti hoon~",
                                    "ठीक है।", ok=False, emotion="neutral"))  # fmt: skip
                return
            book.delete(int(key))
            ctx.report(call, _r(lang, "Forgotten. 🐾", "Bhool gayi. 🐾", "भूल गई।",
                                emotion="neutral"))  # fmt: skip

        ctx.ask(call, question, choices, picked)
        return None  # reports after you pick
    # list: what she remembers (her AI tells you in her own words)
    mems = book.recall(text or "about me", k=8, min_score=0.2) or book.all()[:8]
    if not mems:
        return _r(lang, "I don't remember anything about you yet~ Tell me things!",
                  "Abhi tak kuch yaad nahi hai~ Mujhe batao!", "अभी कुछ याद नहीं है।",
                  emotion="curious")  # fmt: skip
    facts = "Things you remember about them: " + "; ".join(m.text for m in mems)
    return ToolResult(ok=True, say="", facts=facts, emotion="happy")


# --- phrases
def _args(target: str):
    def build(m, said):
        text = next((g for g in m.groups() if g), "").strip(" .!?")
        return {"target": target, "text": text} if (text or target == "list") else None

    return build


intent(
    "memory",
    r"^(?:please\s+|coco,?\s+)?remember\s+that\s+(.+)$",
    r"^(?:please\s+|coco,?\s+)?remember\s+((?:my|i|i'm|i am|our|me)\b.+)$",
    r"^(.+?)\s+yaad\s+rakh(?:na|o|ना)?(?:\s+(?:hai|please))?$",
    r"^yaad\s+rakhna\s+(?:ki\s+)?(.+)$",
    args=_args("add"),
    priority=8,  # ("remember to call mom" stays a reminder: no "that" / "my" there)
)
intent(
    "memory",
    r"\bwhat (?:do|did) you (?:remember|know) about me\b|\bwhat have you remembered\b|"
    r"\bwhat do you remember\b|\b(?:tumhe|tujhe|aapko) mere baare mein kya (?:yaad|pata) hai\b",
    args=lambda m, s: {"target": "list"},
    questions=True,
    priority=8,
)
intent(
    "memory",
    r"^(?:please\s+|coco,?\s+)?forget\s+(?:that|about|what i (?:said|told you) about)\s+(.+)$",
    r"^(.+?)\s+(?:bhool|bhul)\s+(?:jao|ja|jana)$",
    args=_args("forget"),
    priority=8,
)

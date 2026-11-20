"""Turns a question into a streamed, emotion-tagged answer."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable

from coco.brain.backend import BackendError, ChatBackend
from coco.brain.name_filter import NameFilter
from coco.brain.ollama_runtime import ensure_server
from coco.brain.prompt import PromptContext, build_system_prompt, reply_schema
from coco.brain.reply_parser import ReplyParser
from coco.brain.schemas import BrainReply, BrainStatus
from coco.core.bus import EventBus
from coco.core.catdef import CatDef
from coco.core.config import BrainConfig
from coco.core.events import Event, Topic
from coco.core.hindi import looks_hinglish, to_roman
from coco.tools.registry import REGISTRY

log = logging.getLogger(__name__)

_GREETING_WORDS = {"hey", "hi", "hello", "coco", "ok", "okay", "oi", "yo", "kitty"}


def wants_something(question: str) -> bool:
    """False for just her name or a greeting ("Coco.", "Hey Coco"): never act on those."""
    words = re.findall(r"[a-z']+", question.lower())
    return any(w not in _GREETING_WORDS for w in words)


# Words that can be part of asking her to do something (any action).
_ACTION_WORDS = re.compile(
    r"\b(run|runs|running|zoom\w*|race|sprint|walk\w*|stroll|wander|move|sleep\w*|nap\w*|"
    r"rest\w*|bed|tired|lie|home|hammock|get up|wake|jump\w*|hop|climb|window|come|here|"
    r"over|stop\w*|stay|sit|freeze|wait|calm|still|chill|peek\w*|hide|play\w*|hunt\w*|"
    r"chase|catch|pounce|cursor|mouse|stretch\w*|groom\w*|clean|wash|lick|yawn\w*|loaf)\b",
    re.IGNORECASE,
)


# The same in Hinglish (after romanising Devanagari and squeezing doubled vowels).
_ACTION_WORDS_HI = re.compile(
    r"(?<![a-z])(so ?ja|sona|sone|neend|nind|aram|bhag|daud|idh?ar|pas a|ruk|bas|uth|jag|"
    r"khel|chh?up|kod|uchh?al|chadh|khidki|gh(o|u)m|tehl|tahal|angd?r?ai|jamhai)",
    re.IGNORECASE,
)


def mentions_action(question: str) -> bool:
    if _ACTION_WORDS.search(question):
        return True
    roman = re.sub(r"([aeiou])\1+", r"\1", to_roman(question).lower())
    return bool(_ACTION_WORDS_HI.search(roman))


# "how do reminders work?" is a question about a task, not a request to do it. Requests
# phrased as questions ("can you open...", "could you...") are fine: intents.clean() drops
# that polite part first.
_INFO_TOOLS = {"system_status", "explain_screen"}


def asks_about(question: str) -> bool:
    from coco.tools.intents import QUESTION, clean

    return bool(QUESTION.match(clean(question)))


_TELLING = re.compile(r"^(?:i|i'm|im|i am|i've|my|we|we're|it|it's|this|that|there)\b")
_ASKING = re.compile(
    r"\b(?:please|pls|can you|could you|would you|will you|can u|i want you|i need you|"
    r"for me|remind me|help me|let'?s|i want to|i wanna|i need to|i'd like|nudge me|ping me|"
    r"show me|tell me|karo|kar do|kar dena|do na|dena)\b"
)


def tells_not_asks(question: str) -> bool:
    """ "I took a screenshot earlier" / "I forgot my notes at college": telling her
    something, not asking her to do it."""
    q = question.lower().strip()
    return bool(_TELLING.match(q)) and not _ASKING.search(q)


def _looks_up(tool: str, args: dict[str, str]) -> bool:
    """Tools that only look something up may answer questions ("how's my battery?")."""
    return tool in _INFO_TOOLS or args.get("target") in ("list", "status")


DEFAULT_LINES = {
    "offline": "Meow... sorry, my brain is asleep. (I couldn't reach Ollama.)",
    "model_missing": "I don't have my thinking model yet! Run: ollama pull {model}",
    "error": "Mrow? Something went wrong in my head. Try again?",
}


class Brain:
    def __init__(
        self,
        cfg: BrainConfig,
        cat: CatDef,
        bus: EventBus,
        backend: ChatBackend,
        *,
        context: Callable[[], PromptContext] = PromptContext,
        ensure: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.cat = cat
        self.bus = bus
        self.backend = backend
        self.context = context  # called per question: time of day, mood, what she's doing
        self.recall = None  # question -> memories that fit it (app sets it)
        self.clock = clock
        self._ensure = ensure or (
            lambda: (
                ensure_server(cfg.host, cfg.autostart_ollama)
                if hasattr(backend, "client")
                else True
            )
        )
        self._lock = threading.Lock()
        self._current = 0  # id of the question being answered (0 = none)
        self._next_id = 0
        self.history: list[dict[str, str]] = []
        self._last_chat = -1e9
        self.threads: list[threading.Thread] = []  # kept for tests (join)
        self.owner_name = "you"  # set by the app (config.yaml owner_name)
        self._since_name = cfg.name_every  # AI replies since she last said your name
        self.tools: Callable[[], list[str]] = list  # set by the app: the allowed PC tasks

    # --- public API
    def ask(
        self, question: str, *, facts: str = "", image_b64: str = "", tools: bool = True
    ) -> int:
        """Start answering `question` on a worker thread. Returns its request id.

        facts: a tool's result to answer from ("Battery 54 %, charging"); image_b64: a
        screenshot for her to look at; tools=False: no PC tasks in this reply (follow-ups
        of a task never start another one)."""
        question = question.strip()
        with self._lock:
            self._next_id += 1
            rid = self._current = self._next_id
            if self.clock() - self._last_chat > self.cfg.history_reset_after:
                self.history.clear()  # long pause: start a fresh conversation
            self._last_chat = self.clock()
        t = threading.Thread(
            target=self._work,
            args=(rid, question, facts, image_b64, tools),
            daemon=True,
            name=f"coco-brain-{rid}",
        )
        self.threads.append(t)
        t.start()
        return rid

    def cancel(self) -> None:
        with self._lock:
            self._current = 0

    def prewarm(self) -> None:
        """Load the model in the background (bubble opened: you're about to ask)."""

        def run() -> None:
            if self._ensure():
                self.backend.prewarm()

        threading.Thread(target=run, daemon=True, name="coco-brain-prewarm").start()

    def reset_history(self) -> None:
        with self._lock:
            self.history.clear()

    # --- worker
    def _name_filter(self) -> NameFilter | None:
        """Your name is allowed once every `name_every` replies; otherwise filter it out."""
        if self.owner_name.lower() in {"", "you", "your human"}:
            return None
        if self._since_name >= self.cfg.name_every:
            return None
        return NameFilter(self.owner_name)

    def _alive(self, rid: int) -> bool:
        return self._current == rid

    def _status(self, rid: int, state: str, message: str = "") -> None:
        self.bus.post(Event(Topic.BRAIN_STATUS, BrainStatus(state, message, rid)))

    def _fail(self, rid: int, kind: str, detail: str) -> None:
        log.warning("Brain %s: %s", kind, detail)
        line = self.cat.lines.get(f"brain_{kind}") if hasattr(self.cat, "lines") else None
        text = (
            (line or DEFAULT_LINES.get(kind, DEFAULT_LINES["error"]))
            .replace("{model}", self.cfg.model)
            .replace("{name}", self.owner_name)
        )
        self._status(rid, kind, text)
        emotion = "sleepy" if kind == "offline" else "sad"
        self.bus.post(
            Event(Topic.BRAIN_REPLY, BrainReply(text, emotion, final=True, request_id=rid))
        )

    def _work(
        self, rid: int, question: str, facts: str = "", image_b64: str = "", use_tools=True
    ) -> None:
        self._status(rid, "thinking")
        if not self._ensure():
            self._fail(rid, "offline", "Ollama server not reachable")
            return
        with self._lock:
            history = list(self.history[-2 * self.cfg.history_turns :])
        tools = [t for t in self.tools() if t in REGISTRY.specs] if use_tools else []
        tools_text = REGISTRY.describe(tools) if tools else ""
        user: dict = {"role": "user", "content": question}
        if facts:
            user["content"] = (
                f"{question}\n\n[What you found out: {facts}]\n"
                "Tell them in your own words, short and clear."
            )
        if image_b64:
            user["images"] = [image_b64]
        ctx = self.context()
        remember = self.recall is not None
        if remember:
            try:
                ctx.memories = self.recall(question)
            except Exception:  # noqa: BLE001 - memory must never break a reply
                log.exception("recalling memories failed")
        messages = [
            {
                "role": "system",
                "content": build_system_prompt(self.cat, ctx, tools_text, remember=remember),
            },
            *history,
            user,
        ]
        parser = ReplyParser()
        names = self._name_filter()  # None = she may say your name in this reply
        hindi = looks_hinglish(question)  # Hinglish: her voice reads the Devanagari copy
        last_post = float("-inf")  # first partial goes out immediately
        started = self.clock()
        first_token: float | None = None
        try:
            schema = reply_schema(hindi=hindi, tools=tools, remember=remember)
            for chunk in self.backend.stream_chat(messages, schema):
                if not self._alive(rid):
                    log.debug("question %d abandoned", rid)
                    return
                if first_token is None and chunk:
                    first_token = self.clock() - started
                if parser.feed(chunk) and self.clock() - last_post >= 0.05:
                    last_post = self.clock()
                    shown = names.view(parser.text, final=False) if names else parser.text
                    self.bus.post(
                        Event(
                            Topic.BRAIN_REPLY,
                            BrainReply(
                                shown,
                                parser.emotion,
                                final=False,
                                request_id=rid,
                                hindi=hindi,
                                speech=parser.speech_hi,
                            ),
                        )
                    )
                if len(parser.text) > self.cfg.max_reply_chars:
                    break
        except BackendError as exc:
            if self._alive(rid):
                self._fail(rid, exc.kind, str(exc))
            return
        if not self._alive(rid):
            return

        text, emotion = parser.finish()
        if len(text) > self.cfg.max_reply_chars:
            text = text[: self.cfg.max_reply_chars].rsplit(" ", 1)[0] + "…"
        if names:
            text = names.clean(text)
            self._since_name += 1
        elif NameFilter(self.owner_name).mentions(text):
            self._since_name = 0  # she used your name: not again for a while
        else:
            self._since_name += 1
        if not text:
            text, emotion = "Hmm?", "curious"
        log.info(
            "Answered in %.1f s (first token %.2f s), emotion=%s",
            self.clock() - started,
            first_token or -1,
            emotion,
        )
        with self._lock:
            self.history += [
                {"role": "user", "content": question},
                # stored in the same JSON shape the model is asked to produce
                {
                    "role": "assistant",
                    "content": json.dumps({"emotion": emotion, "text": text}, ensure_ascii=False),
                },
            ]
            self.history = self.history[-2 * self.cfg.history_turns :]
            self._last_chat = self.clock()
            if self._current == rid:
                self._current = 0
        action = parser.action
        if action != "none" and not (wants_something(question) and mentions_action(question)):
            # Never act on a greeting or on misheard speech ("Alright, let's start today"
            # -> jump_up): what you said must contain a word that fits some action.
            log.info("Ignoring action %r: %r doesn't ask for it", action, question)
            action = "none"
        if action != "none":
            log.info("The AI asked her to: %s", action)
        tool, tool_args = parser.tool, parser.tool_args
        if tool != "none" and (
            tool not in tools
            or not wants_something(question)
            or not REGISTRY.supported_by_words(tool, question)
            or (asks_about(question) and not _looks_up(tool, tool_args))
            or (
                tells_not_asks(question)
                and not _looks_up(tool, tool_args)
                and tool != "media_control"
            )  # ("this music is too loud" = turn it down)
        ):
            # same idea as actions: the words you said must fit the task
            log.info("Ignoring tool %r: %r doesn't ask for it", tool, question)
            tool, tool_args = "none", {}
        if tool != "none":
            log.info("The AI chose a task: %s %s", tool, tool_args)
        reply = BrainReply(
            text,
            emotion,
            action=action,
            final=True,
            request_id=rid,
            hindi=hindi,
            speech=parser.speech_hi,
            tool=tool,
            tool_args=tool_args,
            remember=parser.remember if not facts and not image_b64 else "",
        )
        self.bus.post(Event(Topic.BRAIN_REPLY, reply))
        self._status(rid, "ready")

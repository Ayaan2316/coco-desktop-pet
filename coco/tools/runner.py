"""Runs tool calls on worker threads and reports results."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from coco.core.events import Event, Topic
from coco.tools.registry import REGISTRY, Registry, ToolCall, ToolResult

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """What tools may use. The app fills in the services; tests pass fakes."""

    cfg: Any = None
    now: Callable[[], datetime] = datetime.now
    services: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:  # ctx.screen, ctx.notes, ...
        try:
            return self.__dict__["services"][name]
        except KeyError:
            raise AttributeError(name) from None


CONFIRM_TEXT = {
    "en": "Are you sure? ({what})",
    "hinglish": "Pakka? ({what})",
}
NOT_ALLOWED = {
    "en": "Meow... I'm not allowed to do that (it's switched off in my tray menu).",
    "hinglish": "Meow... mujhe ye karne ki permission nahi hai (tray menu mein band hai).",
}
CANCELLED = {"en": "Okay, I won't. *tail swish*", "hinglish": "Theek hai, nahi karti."}
FAILED = {"en": "Oops, that didn't work. Meow...", "hinglish": "Oops, ye nahi hua. Meow..."}


class ToolRunner:
    def __init__(
        self,
        bus,
        ctx: ToolContext,
        allowed: Callable[[], list[str]],
        *,
        disabled: Callable[[], list[str]] = list,
        ask_confirm: Callable[[ToolCall, str, Callable[[bool], None]], None] | None = None,
        registry: Registry = REGISTRY,
        threaded: bool = True,
    ) -> None:
        self.bus = bus
        self.ctx = ctx
        self.allowed = allowed
        self.disabled = disabled
        self.ask_confirm = ask_confirm
        self.registry = registry
        self.threaded = threaded  # tests run everything inline
        ctx.services.setdefault("report", self._post)  # late results: ctx.report(call, result)

    def available(self) -> list[str]:
        return self.registry.available(self.allowed(), self.disabled())

    def handle(self, call: ToolCall) -> None:
        lang = "hinglish" if call.lang == "hinglish" else "en"
        if not self.registry.is_allowed(call.tool, self.allowed(), self.disabled()):
            log.info("tool %s refused: not allowed", call.tool)
            self._post(call, ToolResult(ok=False, say=NOT_ALLOWED[lang], emotion="sad"))
            return
        spec = self.registry.specs[call.tool]
        if spec.needs_confirm(call):
            if self.ask_confirm is None:  # nobody to ask: never do risky things silently
                self._post(call, ToolResult(ok=False, say=CANCELLED[lang], emotion="neutral"))
                return
            if spec.confirm_text is not None:
                question = spec.confirm_text(call)
            else:
                what = call.arg("text") or call.arg("target") or call.tool.replace("_", " ")
                question = CONFIRM_TEXT[lang].format(what=what)

            def answered(yes: bool) -> None:
                if yes:
                    self._start(call, spec.needs_qt)
                else:
                    self._post(call, ToolResult(ok=False, say=CANCELLED[lang], emotion="neutral"))

            self.ask_confirm(call, question, answered)
            return
        self._start(call, spec.needs_qt)

    def _start(self, call: ToolCall, needs_qt: bool) -> None:
        if needs_qt or not self.threaded:
            self._run(call)  # the app calls handle() on the Qt main thread
        else:
            threading.Thread(target=self._run, args=(call,), daemon=True,
                             name=f"coco-tool-{call.tool}").start()  # fmt: skip

    def _run(self, call: ToolCall) -> None:
        lang = "hinglish" if call.lang == "hinglish" else "en"
        try:
            result = self.registry.run(call, self.ctx, self.allowed(), self.disabled())
        except Exception:
            log.exception("tool %s failed", call.tool)
            result = ToolResult(ok=False, say=FAILED[lang], emotion="sad")
        if result is None:
            return  # the tool reports later through ctx.report (e.g. after you drag an area)
        self._post(call, result)

    def _post(self, call: ToolCall, result: ToolResult) -> None:
        log.info("tool %s %s -> ok=%s %r", call.tool, call.args, result.ok, result.say[:60])
        self.bus.post(Event(Topic.TOOL_RESULT, (call, result)))

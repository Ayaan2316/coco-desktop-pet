"""Model backends: Ollama, or a scripted fake for tests."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from coco.core.config import BrainConfig

log = logging.getLogger(__name__)


class BackendError(Exception):
    """kind: "offline" (server unreachable), "model_missing", or "error"."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class Health:
    server_up: bool
    model_present: bool
    detail: str = ""


class ChatBackend(Protocol):
    def health(self) -> Health: ...

    def stream_chat(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Iterator[str]:
        """Yield the reply's content a chunk at a time. Raises BackendError."""
        ...

    def prewarm(self) -> None:
        """Load the model into GPU memory so the first reply is fast."""
        ...


# Ollama


class OllamaBackend:
    def __init__(self, cfg: BrainConfig) -> None:
        import ollama  # imported here so the tests never need the package or a server

        self.cfg = cfg
        self._ollama = ollama
        self.client = ollama.Client(host=cfg.host, timeout=cfg.request_timeout)
        self._think_supported = True

    def _model_names(self) -> list[str]:
        return [m.model for m in self.client.list().models]

    def health(self) -> Health:
        try:
            names = self._model_names()
        except Exception as exc:  # connection refused, timeout...
            return Health(False, False, str(exc))
        wanted = self.cfg.model if ":" in self.cfg.model else f"{self.cfg.model}:latest"
        return Health(True, wanted in names, ", ".join(names))

    def _options(self) -> dict[str, Any]:
        # Prewarm and chat must use the same num_ctx: a different context size makes
        # Ollama reload the whole model (slow).
        return {"temperature": self.cfg.temperature, "num_ctx": self.cfg.num_ctx}

    def _call(self, messages, schema, think: bool | None):
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": True,
            "format": schema,
            "options": self._options(),
            "keep_alive": self.cfg.keep_alive,
        }
        if think is not None:
            kwargs["think"] = think
        return self.client.chat(**kwargs)

    def stream_chat(self, messages, schema) -> Iterator[str]:
        think = self.cfg.think if self._think_supported else None
        try:
            try:
                stream = self._call(messages, schema, think)
                first = next(stream, None)
            except self._ollama.ResponseError as exc:
                # Models without a thinking mode reject `think`: retry without it.
                if think is not None and "think" in str(exc).lower():
                    self._think_supported = False
                    stream = self._call(messages, schema, None)
                    first = next(stream, None)
                elif exc.status_code == 500 and "terminated" in str(exc).lower():
                    # The GPU runner sometimes fails while loading; one retry is enough.
                    log.warning("Ollama's model runner crashed; retrying once: %s", exc)
                    stream = self._call(messages, schema, think)
                    first = next(stream, None)
                else:
                    raise
            if first is not None:
                yield first.message.content or ""
            for part in stream:
                yield part.message.content or ""
        except self._ollama.ResponseError as exc:
            if exc.status_code == 404 or "not found" in str(exc).lower():
                raise BackendError("model_missing", str(exc)) from exc
            raise BackendError("error", str(exc)) from exc
        except (ConnectionError, OSError) as exc:
            raise BackendError("offline", str(exc)) from exc
        except Exception as exc:  # httpx errors (timeouts, refused) land here
            name = type(exc).__name__
            kind = "offline" if "Connect" in name or "Timeout" in name else "error"
            raise BackendError(kind, f"{name}: {exc}") from exc

    def prewarm(self) -> None:
        try:
            # An empty prompt just loads the model and keeps it loaded for keep_alive.
            self.client.generate(
                model=self.cfg.model,
                prompt="",
                keep_alive=self.cfg.keep_alive,
                options=self._options(),
            )
        except Exception as exc:
            log.debug("prewarm failed: %s", exc)


# Fake (tests, --fake-brain)


def _default_script(question: str) -> tuple[str, str]:
    q = question.lower()
    if any(w in q for w in ("sad", "bad", "fail", "tired")):
        return "sad", "Oh no... *nuzzles your hand* It'll be okay. Want to tell me more?"
    if any(w in q for w in ("why", "how", "what", "explain")):
        return "curious", (
            "Ooh, good question! I'm the pretend brain right now (`--fake-brain`), so I "
            "can't really answer, but the real me will.\n\n- point one\n- point two"
        )
    return "happy", "Hi! I'm running on my fake brain, but I'm listening. 🐾"


class FakeBackend:
    """Streams scripted replies as JSON in small chunks, like a real model would."""

    def __init__(
        self,
        script: Callable[[str], tuple[str, str]] = _default_script,
        chunk_size: int = 6,
        delay: float = 0.02,
        fail_with: BackendError | None = None,
    ) -> None:
        self.script = script
        self.chunk_size = chunk_size
        self.delay = delay
        self.fail_with = fail_with
        self.calls: list[list[dict[str, str]]] = []
        self.prewarmed = 0

    def health(self) -> Health:
        if self.fail_with is not None and self.fail_with.kind == "offline":
            return Health(False, False, "fake offline")
        return Health(True, True, "fake")

    def stream_chat(self, messages, schema) -> Iterator[str]:
        self.calls.append(list(messages))
        if self.fail_with is not None:
            raise self.fail_with
        emotion, text = self.script(messages[-1]["content"])
        payload = json.dumps({"emotion": emotion, "text": text}, ensure_ascii=False)
        for i in range(0, len(payload), self.chunk_size):
            if self.delay:
                time.sleep(self.delay)
            yield payload[i : i + self.chunk_size]

    def prewarm(self) -> None:
        self.prewarmed += 1

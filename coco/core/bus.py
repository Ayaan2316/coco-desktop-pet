"""A small publish/subscribe event bus."""

from __future__ import annotations

import logging
import queue
from collections import defaultdict
from collections.abc import Callable

from coco.core.events import Event

log = logging.getLogger(__name__)

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._queue: queue.SimpleQueue[Event] = queue.SimpleQueue()
        # Called (from the posting thread!) after every post(). The app sets it to a
        # thread-safe "wake up the main loop" signal, so events from the AI worker are
        # handled right away instead of at the next (possibly 250 ms away) frame.
        self.on_post: Callable[[], None] | None = None

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to an exact topic, a prefix wildcard ("input.*"), or everything ("*").

        Returns a function that unsubscribes again.
        """
        self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers.get(topic, []):
                self._handlers[topic].remove(handler)

        return unsubscribe

    def publish(self, event: Event) -> None:
        """Deliver `event` right now to every matching handler (main thread only)."""
        for handler in self._matching(event.topic):
            try:
                handler(event)
            except Exception:  # one broken handler must not take Coco down
                log.exception("Event handler failed for %s", event.topic)

    def post(self, event: Event) -> None:
        """Thread-safe: queue `event` for delivery on the main thread."""
        self._queue.put(event)
        if self.on_post is not None:
            self.on_post()

    def drain(self, limit: int = 200) -> int:
        """Deliver queued events (call from the main thread). Returns how many ran."""
        count = 0
        while count < limit:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            self.publish(event)
            count += 1
        return count

    def _matching(self, topic: str) -> list[Handler]:
        handlers = list(self._handlers.get(topic, []))
        prefix = topic.split(".", 1)[0]
        handlers += self._handlers.get(prefix + ".*", [])
        handlers += self._handlers.get("*", [])
        return handlers

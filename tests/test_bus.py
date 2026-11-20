"""Event bus: exact topics, wildcards, thread-safe post + drain, broken handlers."""

import threading

from coco.core.bus import EventBus
from coco.core.events import Event


def test_publish_reaches_exact_and_wildcard_subscribers():
    bus = EventBus()
    seen = []
    bus.subscribe("input.click", lambda e: seen.append(("exact", e.topic)))
    bus.subscribe("input.*", lambda e: seen.append(("prefix", e.topic)))
    bus.subscribe("*", lambda e: seen.append(("all", e.topic)))
    bus.subscribe("desktop.*", lambda e: seen.append(("other", e.topic)))
    bus.publish(Event("input.click"))
    assert sorted(seen) == [
        ("all", "input.click"),
        ("exact", "input.click"),
        ("prefix", "input.click"),
    ]


def test_unsubscribe():
    bus = EventBus()
    seen = []
    unsub = bus.subscribe("a.b", seen.append)
    unsub()
    bus.publish(Event("a.b"))
    assert seen == []


def test_post_from_other_thread_is_delivered_on_drain():
    bus = EventBus()
    seen = []
    bus.subscribe("brain.reply", lambda e: seen.append(e.payload))
    t = threading.Thread(target=lambda: bus.post(Event("brain.reply", "hi")))
    t.start()
    t.join()
    assert seen == []  # nothing delivered until the main thread drains
    assert bus.drain() == 1
    assert seen == ["hi"]


def test_broken_handler_does_not_stop_others():
    bus = EventBus()
    seen = []

    def boom(_):
        raise RuntimeError("bad handler")

    bus.subscribe("x.y", boom)
    bus.subscribe("x.y", seen.append)
    bus.publish(Event("x.y"))
    assert len(seen) == 1

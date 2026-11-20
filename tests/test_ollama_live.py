"""One real question to the real model. Skipped automatically unless the Ollama server is
running and the configured model is pulled (so the suite stays fast and offline)."""

import time

import pytest

from coco.brain.ollama_runtime import server_up
from coco.brain.schemas import EMOTIONS
from coco.core.config import load_config

CFG = load_config()


def _model_available() -> bool:
    if not server_up(CFG.brain.host):
        return False
    try:
        from coco.brain.backend import OllamaBackend

        return OllamaBackend(CFG.brain).health().model_present
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _model_available(), reason="Ollama/model not available")


def test_real_model_answers_with_a_valid_emotion():
    from coco.brain.backend import OllamaBackend
    from coco.brain.brain import Brain
    from coco.core.bus import EventBus
    from coco.core.catdef import load_cat
    from coco.core.events import Topic

    bus = EventBus()
    finals = []
    bus.subscribe(Topic.BRAIN_REPLY, lambda e: e.payload.final and finals.append(e.payload))
    brain = Brain(CFG.brain, load_cat("coco"), bus, OllamaBackend(CFG.brain))
    brain.ask("Thanks Coco! Say hi in five words.")
    deadline = time.monotonic() + 120  # includes loading the model from disk
    while not finals and time.monotonic() < deadline:
        bus.drain()
        time.sleep(0.05)
    assert finals, "no reply within 2 minutes"
    reply = finals[-1]
    assert reply.text.strip()
    assert reply.emotion in EMOTIONS

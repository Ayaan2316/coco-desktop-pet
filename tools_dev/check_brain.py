r"""Check the brain from the console: Ollama status, model, speed.

.venv\Scripts\python tools_dev\check_brain.py
.venv\Scripts\python tools_dev\check_brain.py "what is a diode?"
.venv\Scripts\python tools_dev\check_brain.py --samples
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coco.brain.backend import OllamaBackend  # noqa: E402
from coco.brain.brain import Brain  # noqa: E402
from coco.brain.ollama_runtime import ensure_server  # noqa: E402
from coco.brain.prompt import PromptContext  # noqa: E402
from coco.core.bus import EventBus  # noqa: E402
from coco.core.catdef import load_cat  # noqa: E402
from coco.core.config import load_config  # noqa: E402
from coco.core.events import Topic  # noqa: E402

SAMPLES = [  # (question, emotions that would make sense)
    ("hi coco!", {"happy", "love"}),
    ("thank you so much, you're the best", {"love", "happy"}),
    ("what does a resistor do?", {"curious", "neutral", "happy"}),
    ("explain Ohm's law in one line", {"curious", "neutral", "happy"}),
    ("my exam went really badly today", {"sad"}),
    ("I failed my lab practical :(", {"sad"}),
    ("guess what, I got an internship!!", {"happy", "surprised", "love"}),
    ("I'm so tired, it's been a long day", {"sleepy", "sad", "love"}),
    ("what's 17 times 23?", {"neutral", "curious", "happy"}),
    ("can you write a python function that reverses a string?", {"neutral", "happy", "curious"}),
    ("why is the sky blue?", {"curious", "neutral"}),
    ("you're a bad cat", {"sad", "angry", "surprised"}),
    ("I love you coco", {"love", "happy"}),
    ("a dog just walked past the window", {"surprised", "curious", "angry"}),
    ("what's the difference between AC and DC?", {"curious", "neutral"}),
    ("should I go to sleep?", {"sleepy", "love", "neutral", "curious"}),
    ("tell me a joke", {"happy"}),
    ("my friend is sick", {"sad", "love"}),
    ("how does a transistor work?", {"curious", "neutral"}),
    ("good morning!", {"happy", "love"}),
]


def ask(brain: Brain, bus: EventBus, question: str) -> tuple[str, str, float, float]:
    """Ask synchronously; returns (text, emotion, first_text_seconds, total_seconds)."""
    got: dict = {}
    t0 = time.perf_counter()

    def on_reply(ev) -> None:
        r = ev.payload
        if "first" not in got and r.text:
            got["first"] = time.perf_counter() - t0
        if r.final:
            got["final"] = r

    bus.subscribe(Topic.BRAIN_REPLY, on_reply)
    brain.ask(question)
    while "final" not in got:
        bus.drain()
        time.sleep(0.01)
    bus._handlers[Topic.BRAIN_REPLY].remove(on_reply)
    r = got["final"]
    return r.text, r.emotion, got.get("first", -1), time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="Hi Coco! What's a capacitor, in one line?")
    ap.add_argument("--samples", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    print(f"Ollama at {cfg.brain.host}: ", end="", flush=True)
    if not ensure_server(cfg.brain.host, cfg.brain.autostart_ollama):
        print("NOT reachable (is Ollama installed?)")
        return 1
    backend = OllamaBackend(cfg.brain)
    health = backend.health()
    print(f"up. Model {cfg.brain.model}: {'present' if health.model_present else 'MISSING'}")
    if not health.model_present:
        print(f"  -> run: ollama pull {cfg.brain.model}   (installed: {health.detail})")
        return 1

    bus = EventBus()
    brain = Brain(
        cfg.brain,
        load_cat(cfg.cat),
        bus,
        backend,
        context=lambda: PromptContext(owner=cfg.owner_name),
    )
    print("Loading the model (first reply is slower)...")
    t0 = time.perf_counter()
    backend.prewarm()
    print(f"  loaded in {time.perf_counter() - t0:.1f} s")

    questions = SAMPLES if args.samples else [(args.question, None)]
    ok_emotion = 0
    for q, expected in questions:
        brain.reset_history()
        text, emotion, first, total = ask(brain, bus, q)
        mark = ""
        if expected is not None:
            good = emotion in expected
            ok_emotion += good
            mark = "  OK" if good else f"  (expected {'/'.join(sorted(expected))})"
        print(f"\nQ: {q}\n[{emotion}]{mark}  first text {first:.2f} s, total {total:.2f} s")
        print(f"A: {text}")
    if args.samples:
        print(f"\nEmotion fits: {ok_emotion}/{len(questions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

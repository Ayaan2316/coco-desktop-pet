"""She shouldn't say your name in every reply: the filter and how the brain uses it."""

from coco.brain.backend import FakeBackend
from coco.brain.brain import Brain
from coco.brain.name_filter import NameFilter
from coco.brain.schemas import BrainReply
from coco.core.bus import EventBus
from coco.core.catdef import load_cat
from coco.core.config import BrainConfig
from coco.core.events import Topic

F = NameFilter("Sam")


def test_name_is_removed_naturally():
    cases = {
        "Anytime, Sam! *slow blink*": "Anytime! *slow blink*",
        "Sam, that's a great idea.": "That's a great idea.",
        "Hey Sam! Want to play?": "Hey! Want to play?",
        "Good job. Sam, you did it!": "Good job. You did it!",
        "Sam's code looks fine.": "Your code looks fine.",
        "I believe in you, sam.": "I believe in you.",
        "A capacitor stores energy.": "A capacitor stores energy.",
    }
    for raw, want in cases.items():
        assert F.clean(raw) == want, raw


def test_streamed_text_never_changes_once_shown():
    for full in ("Sure thing, Sam! Here you go.", "Sam, look at this!", "Hi Sam, meow~"):
        shown = ""
        for i in range(1, len(full) + 1):
            now = F.view(full[:i], final=False)
            assert now.startswith(shown), (full[:i], shown, now)  # only ever grows
            shown = now
        assert F.view(full, final=True).startswith(shown)
        assert "sam" not in F.view(full, final=True).lower()


def test_brain_allows_the_name_only_once_in_a_while():
    bus = EventBus()
    got: list[BrainReply] = []
    bus.subscribe(Topic.BRAIN_REPLY, lambda e: got.append(e.payload))
    backend = FakeBackend(script=lambda q: ("happy", "Okay, Sam! Meow~"), chunk_size=2, delay=0)
    brain = Brain(BrainConfig(name_every=3), load_cat("coco"), bus, backend, ensure=lambda: True)
    brain.owner_name = "Sam"
    finals = []
    for _ in range(5):
        got.clear()
        brain.ask("hello there")
        for t in brain.threads:
            t.join(timeout=5)
        bus.drain()
        finals.append([r for r in got if r.final][-1].text)
        if finals[-1] == "Okay! Meow~":  # filtered: not even a half name was ever shown
            assert not any("Ay" in r.text for r in got)
    assert finals[0] == "Okay, Sam! Meow~"  # allowed: she hasn't said it for a while
    assert finals[1:4] == ["Okay! Meow~"] * 3  # then not for the next 3 replies
    assert finals[4] == "Okay, Sam! Meow~"

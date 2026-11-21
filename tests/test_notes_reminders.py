"""Notes, reminders and timers: phrases, the tools, the store, notes.md (temp folders)."""

from datetime import datetime

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.tools.intents import parse_task
from coco.tools.notes import NotesBook, best_match
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.reminders import ReminderBook
from coco.tools.runner import ToolContext
from coco.tools.store import Store

NOW = datetime(2026, 11, 21, 14, 0)  # Saturday 2 pm
TOOLS = ["notes", "reminder", "open_app", "web_search", "screenshot", "explain_screen"]


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("note down: buy a new ESP32", {"target": "add", "text": "buy a new esp32"}),
        ("Coco, make a note to call the lab assistant",
         {"target": "add", "text": "call the lab assistant"}),
        ("add milk to my list", {"target": "add", "text": "milk"}),
        ("note kar lo ESP32 lena hai", {"target": "add", "text": "esp32 lena hai"}),
        ("resistors khareedne hai note kar lo",
         {"target": "add", "text": "resistors khareedne hai"}),
        ("what's on my list?", {"target": "list"}),
        ("show my notes", {"target": "list"}),
        ("meri list mein kya hai", {"target": "list"}),
        ("notes dikhao", {"target": "list"}),
        ("mark the esp32 one as done", {"target": "done", "text": "esp32 one"}),
        ("esp32 wala ho gaya", {"target": "done", "text": "esp32"}),
        ("delete the milk note", {"target": "delete", "text": "milk"}),
        ("delete all my notes", {"target": "delete", "text": "all"}),
    ],
)  # fmt: skip
def test_note_phrases(said, args):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == "notes", said
    assert call.args == args


@pytest.mark.parametrize(
    ("said", "target"),
    [
        ("remind me at 6 to submit the assignment", "add"),
        ("remind me to call mom in 20 minutes", "add"),
        ("6 baje yaad dilana assignment submit karna hai", "add"),
        ("set a timer for 5 minutes", "timer"),
        ("5 minute ka timer laga do", "timer"),
        ("what reminders do I have?", "list"),
        ("reminders dikhao", "list"),
        ("cancel the timer", "cancel"),
        ("cancel the 6 o'clock reminder", "cancel"),
        ("cancel all reminders", "clear"),
    ],
)
def test_reminder_phrases(said, target):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == "reminder", said
    assert call.args["target"] == target


@pytest.fixture
def ctx(tmp_path):
    clock = [NOW.timestamp()]
    store = Store(tmp_path / "coco.db", clock=lambda: clock[0])
    c = ToolContext(
        now=lambda: datetime.fromtimestamp(clock[0]),
        services={
            "notes": NotesBook(store, tmp_path / "Documents" / "Coco" / "notes.md"),
            "reminders": ReminderBook(store, clock=lambda: clock[0]),
        },
    )
    c.services["clock"] = clock
    yield c
    store.close()


def run(ctx, tool, said="", lang="en", **args):
    return REGISTRY.run(ToolCall(tool, args, said=said, lang=lang), ctx, TOOLS)


def test_notes_round_trip_and_markdown(ctx, tmp_path):
    assert "Noted" in run(ctx, "notes", target="add", text="buy a new ESP32").say
    run(ctx, "notes", target="add", text="call the lab assistant")
    listing = run(ctx, "notes", target="list").say
    assert "1. buy a new ESP32" in listing and "2. call the lab assistant" in listing
    assert "Ticked off: buy a new ESP32" in run(ctx, "notes", target="done", text="esp32").say
    md = (tmp_path / "Documents" / "Coco" / "notes.md").read_text(encoding="utf-8")
    assert "- [ ] call the lab assistant" in md and "- [x] buy a new ESP32" in md
    assert "couldn't find" in run(ctx, "notes", target="done", text="banana").say
    assert run(ctx, "notes", target="delete", text="all").say.startswith("Done, I deleted all")
    assert "empty" in run(ctx, "notes", target="list").say


def test_deleting_notes_asks_first():
    spec = REGISTRY.specs["notes"]
    assert spec.needs_confirm(ToolCall("notes", {"target": "delete", "text": "milk"}))
    assert not spec.needs_confirm(ToolCall("notes", {"target": "add", "text": "milk"}))
    assert "ALL" in spec.confirm_text(ToolCall("notes", {"target": "delete", "text": "all"}))
    assert REGISTRY.specs["reminder"].needs_confirm(ToolCall("reminder", {"target": "clear"}))


def test_which_note():
    from coco.tools.store import Note

    notes = [Note(1, "buy a new ESP32", False, 0), Note(2, "call mom", False, 0)]
    assert best_match(notes, "the esp32 one").id == 1
    assert best_match(notes, "the second one").id == 2
    assert best_match(notes, "number 1").id == 1
    assert best_match(notes, "last").id == 2
    assert best_match(notes, "pizza") is None


def test_reminder_flow_with_snooze_and_restart(ctx, tmp_path):
    r = run(ctx, "reminder", said="remind me at 6 to submit the assignment", target="add")
    assert r.say == ("Okay! I'll remind you at 6:00 pm: submit the assignment. "
                     "*writes on a sticky note*")  # fmt: skip
    book = ctx.reminders
    assert book.take_due() == []  # not yet
    ctx.clock[0] = datetime(2026, 11, 21, 18, 0, 5).timestamp()
    due = book.take_due()
    assert [d.text for d in due] == ["submit the assignment"]
    assert book.take_due() == []  # handed out once
    book.snooze(due[0].id, 10)
    ctx.clock[0] += 11 * 60
    again = book.take_due()
    assert [d.id for d in again] == [due[0].id]
    # Coco restarts before you answer: it comes back
    store2 = Store(tmp_path / "coco.db", clock=lambda: ctx.clock[0])
    store2.reset_shown()
    assert [d.id for d in ReminderBook(store2, clock=lambda: ctx.clock[0]).take_due()] == [
        due[0].id
    ]
    book.done(due[0].id)
    assert book.pending() == []
    store2.close()


def test_hinglish_reminder_and_timer(ctx):
    r = run(ctx, "reminder", said="kal subah 9 baje yaad dilana ki lab report submit karni hai",
            lang="hinglish", target="add")  # fmt: skip
    assert r.say == "Theek hai! kal subah 9:00 baje yaad dila dungi: lab report submit karni hai."
    assert r.speech_hi
    t = run(ctx, "reminder", said="set a timer for 5 minutes", target="timer")
    assert t.say.startswith("Timer set: 5 minutes")
    items = ctx.reminders.pending()
    assert [i.kind for i in items] == ["timer", "reminder"]  # sorted by when they're due
    c = run(ctx, "reminder", said="cancel the timer", target="cancel", text="timer")
    assert c.say == "Cancelled: 5 minutes timer." and len(ctx.reminders.pending()) == 1


def test_reminder_without_a_time_asks_when(ctx):
    r = run(ctx, "reminder", said="remind me to drink water", target="add")
    assert not r.ok and "When should I remind you" in r.say


def test_ai_args_work_too(ctx):
    """The brain may fill `when` and `text` itself (fuzzy requests)."""
    r = run(ctx, "reminder", said="ugh I keep forgetting water", target="add",
            when="in an hour", text="drink water")  # fmt: skip
    assert "drink water" in r.say and ctx.reminders.pending()[0].text == "drink water"

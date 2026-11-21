"""Commands ("Coco, run!"): the phrase matcher and the engine carrying them out."""

import pytest

from coco.behaviour.commands import ACTIONS, parse_command
from coco.brain.reply_parser import ReplyParser, normalize_action
from coco.brain.schemas import BrainReply
from coco.core.events import Topic
from coco.core.geometry import Point, Rect
from coco.desktop.base import WindowInfo
from tests.conftest import Sim


@pytest.mark.parametrize(
    "text,action",
    [
        ("run", "run"), ("Coco, run!", "run"), ("hey coco can you run please", "run"),
        ("zoomies!", "run"), ("go for a walk", "walk"), ("walk around", "walk"),
        ("go to sleep", "sleep"), ("Coco go to bed", "sleep"), ("take a nap", "sleep"),
        ("nap time", "sleep"), ("go home", "sleep"), ("wake up", "get_up"),
        ("get out of bed", "get_up"), ("jump on my window", "jump_up"), ("jump!", "jump_up"),
        ("come here", "come_here"), ("come here kitty", "come_here"), ("stop", "stop"),
        ("sit down", "stop"), ("stay", "stop"), ("peekaboo", "peek"),
        ("hide and seek", "peek"), ("play with me", "play"), ("catch the cursor", "play"),
        ("stretch", "stretch"), ("clean yourself", "groom"), ("yawn", "yawn"),
        ("could you please stretch now", "stretch"), ("loaf", "loaf"),
    ],
)  # fmt: skip
def test_commands_are_recognised(text, action):
    assert parse_command(text) == action


@pytest.mark.parametrize(
    "text",
    [
        "why do cats sleep so much?", "what is a capacitor", "my code won't run",
        "run the tests for me and tell me what failed", "I need to sleep", "how do I walk",
        "do you like to run?", "tell me a joke", "", "   ", "the walk was nice",
    ],
)  # fmt: skip
def test_questions_and_chat_are_not_commands(text):
    assert parse_command(text) is None


def test_every_action_has_a_pattern():
    names = {
        parse_command(w)
        for w in (
            "run",
            "walk",
            "sleep",
            "get up",
            "jump",
            "come here",
            "stop",
            "peek",
            "play",
            "stretch",
            "groom",
            "yawn",
            "loaf",
        )
    }
    assert names == set(ACTIONS)


def test_ai_action_field_is_parsed_and_restricted():
    p = ReplyParser()
    for chunk in ('{"emotion": "sleepy", "act', 'ion": "sleep", "text": "Nap ', 'time"}'):
        p.feed(chunk)
    assert p.finish() == ("Nap time", "sleepy") and p.action == "sleep"
    assert normalize_action("delete files") == "none"  # not on the list: ignored
    assert normalize_action("Come Here") == "come_here"


# --- engine


def test_run_command_from_chat_runs_without_thinking():
    sim = Sim()
    sim.send(Topic.CHAT_OPEN)
    sim.send(Topic.CHAT_ASK, "Coco, run!")
    sim.step()
    assert sim.state == "run"
    assert sim.eng.last_command == ("run", True, "")


def test_commands_work_even_when_wandering_is_paused():
    sim = Sim()
    sim.eng.paused = True
    ok, _ = sim.eng.command("walk")
    assert ok and sim.state == "walk"


def test_run_while_asleep_in_bed_wakes_hops_out_then_runs():
    sim = Sim(start_in_bed=True)
    sim.run(3, until=lambda s: s.state == "sit")
    sim.eng.force("sleep")
    ok, _ = sim.eng.command("run")
    assert ok and sim.state == "wake_up"
    assert sim.run(10, until=lambda s: s.state == "run")
    assert "jump" in sim.seen  # hopped out of the bed on the way


def test_sleep_command_walks_home_and_naps():
    sim = Sim()
    ok, _ = sim.eng.command("sleep")
    assert ok
    assert sim.run(60, until=lambda s: s.state == "sleep")
    assert sim.eng.surface.kind == "bed"
    assert sim.eng.command("sleep") == (False, "already_asleep")


def test_get_up_from_bed():
    sim = Sim(start_in_bed=True)
    sim.run(3, until=lambda s: s.state == "sit")
    assert sim.eng.command("get_up")[0]
    assert sim.run(5, until=lambda s: s.eng.surface is not None and s.eng.surface.kind == "ground")
    assert sim.eng.command("get_up") == (False, "already_up")


def test_come_here_walks_under_the_cursor():
    sim = Sim(cursor=Point(400, 700))
    ok, _ = sim.eng.command("come_here")
    assert ok
    assert sim.run(20, until=lambda s: s.state == "sit")
    assert abs(sim.eng.body.x - 400) < 2


def test_stop_interrupts_a_walk():
    sim = Sim()
    sim.eng.command("walk")
    sim.step()
    assert sim.eng.command("stop") == (True, "")
    assert sim.state == "sit"


def test_jump_up_needs_a_window():
    sim = Sim()
    assert sim.eng.command("jump_up") == (False, "no_window")
    sim2 = Sim([WindowInfo(3, Rect(500, 600, 1300, 1000))], foreground=3)
    assert sim2.eng.command("jump_up")[0]
    assert sim2.run(4, until=lambda s: s.state == "land")
    assert sim2.eng.surface.id == 3


def test_play_walks_over_then_stalks():
    sim = Sim(cursor=Point(200, 950))  # far to the left, near her floor
    ok, _ = sim.eng.command("play")
    assert ok
    assert sim.run(20, until=lambda s: s.state == "stalk")


def test_refused_while_held():
    sim = Sim()
    sim.send(Topic.DRAG_START)
    sim.fake.cursor = Point(900, 500)
    sim.step()
    assert sim.eng.command("run") == (False, "held")


def test_ai_reply_with_action_makes_her_do_it():
    sim = Sim()
    sim.send(Topic.CHAT_ASK, "you look so tired, maybe rest a bit?")  # not a short command
    sim.step()
    assert sim.state == "think"
    sim.send(Topic.BRAIN_REPLY, BrainReply("Okay, nap time!", "sleepy", action="sleep", final=True))
    sim.step()
    assert sim.state in ("walk", "run")  # on her way to bed
    assert sim.run(60, until=lambda s: s.state == "sleep")


@pytest.mark.parametrize(
    "text", ["can you go take a nap", "go rest", "rest a bit", "Coco, rest for a while"]
)
def test_more_ways_to_say_sleep(text):
    assert parse_command(text) == "sleep"

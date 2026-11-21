"""System status + media control (fakes: tests never change your volume or press keys)."""

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.brain.prompt import PromptContext, build_system_prompt
from coco.core.catdef import load_cat
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext
from coco.tools.system import Status, describe

TOOLS = ["system_status", "media_control", "open_app", "web_search", "reminder", "notes",
         "focus", "screenshot", "explain_screen"]  # fmt: skip


@pytest.mark.parametrize(
    ("said", "target"),
    [
        ("how's my battery?", "battery"),
        ("how much battery do I have", "battery"),
        ("battery kitni hai?", "battery"),
        ("how hot is my GPU?", "gpu"),
        ("GPU kitna garam hai", "gpu"),
        ("is my laptop busy?", "all"),
        ("system status", "all"),
        ("what's my ram usage", "ram"),
    ],
)
def test_status_phrases(said, target):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == "system_status", said
    assert call.args == {"target": target}


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("pause the music", {"target": "pause"}),
        ("stop the music", {"target": "pause"}),
        ("pause", {"target": "pause"}),
        ("play music", {"target": "play"}),
        ("resume", {"target": "play"}),
        ("next song", {"target": "next"}),
        ("skip", {"target": "next"}),
        ("previous song", {"target": "previous"}),
        ("volume up", {"target": "volume_up"}),
        ("louder", {"target": "volume_up"}),
        ("volume down", {"target": "volume_down"}),
        ("volume 40", {"target": "set_volume", "amount": "40"}),
        ("set the volume to 25%", {"target": "set_volume", "amount": "25"}),
        ("mute", {"target": "mute"}),
        ("unmute", {"target": "unmute"}),
        ("gaana band karo", {"target": "pause"}),
        ("gaana chalao", {"target": "play"}),
        ("agla gaana", {"target": "next"}),
        ("awaaz badhao", {"target": "volume_up"}),
        ("awaaz kam karo", {"target": "volume_down"}),
        ("awaaz 30 karo", {"target": "set_volume", "amount": "30"}),
    ],
)
def test_media_phrases(said, args):
    call = parse_task(said, TOOLS)
    assert call is not None and call.tool == "media_control", said
    assert call.args == args


def test_play_on_youtube_is_a_search_not_media():
    assert parse_task("play lofi on youtube", TOOLS).tool == "web_search"


class FakeMedia:
    def __init__(self):
        self.keys, self.vol, self.muted = [], 50.0, False

    def key(self, name):
        self.keys.append(name)

    def volume(self):
        return self.vol

    def set_volume(self, p):
        self.vol = p

    def set_mute(self, m):
        self.muted = m


def test_media_tool():
    media = FakeMedia()
    ctx = ToolContext(services={"media": media})
    run = lambda **a: REGISTRY.run(ToolCall("media_control", a), ctx, TOOLS)  # noqa: E731
    run(target="pause")
    run(target="next")
    assert media.keys == ["play_pause", "next"]
    assert run(target="volume_up").say == "Volume 60%." and media.vol == 60
    run(target="set_volume", amount="120")
    assert media.vol == 100  # clamped
    run(target="mute")
    assert media.muted


def test_status_facts_and_tool():
    s = Status(battery=15, plugged=False, cpu=12, ram=55, ram_used_gb=17.3, ram_total_gb=31.4,
               gpu_temp=53, gpu_use=4, gpu_mem_used_gb=7.4, gpu_mem_total_gb=8)  # fmt: skip
    assert describe(s, "battery") == "Battery 15% (NOT plugged in)."
    assert "GPU 53°C" in describe(s, "gpu") and "CPU 12% busy" in describe(s)
    ctx = ToolContext(services={"read_status": lambda: s})
    r = REGISTRY.run(ToolCall("system_status", {"target": "battery"}), ctx, TOOLS)
    assert r.facts.endswith("Battery 15% (NOT plugged in).") and r.say  # say = offline fallback
    assert describe(Status()) == "I couldn't read that on this PC."


def test_low_battery_goes_into_her_prompt():
    text = build_system_prompt(
        load_cat("coco"),
        PromptContext(owner="Sam", battery="Their laptop battery is at 7% and not charging."),
    )
    assert "battery is at 7%" in text

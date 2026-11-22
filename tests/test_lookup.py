"""Web lookups (opt-in): Wikipedia + Brave with a fake network, phrases, and the switch."""

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.tools.intents import parse_task
from coco.tools.lookup import WebLookup
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext

WIKI_SEARCH = {"query": {"search": [{"title": "ESP32", "snippet": "a <b>chip</b>"},
                                    {"title": "ESP8266", "snippet": "older"}]}}  # fmt: skip
BRAVE = {"web": {"results": [{"title": "ISRO news", "description": "Chandrayaan-4 <strong>"
                              "approved</strong>", "url": "https://x.example/1",
                              "profile": {"name": "Example News"}}]}}  # fmt: skip


class Net:
    def __init__(self, fail_brave=False):
        self.calls = []
        self.fail_brave = fail_brave

    def __call__(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        if "api.php" in url:
            return WIKI_SEARCH
        if "summary/ESP32" in url:
            page = "https://en.wikipedia.org/wiki/ESP32"
            return {"extract": "ESP32 is a family of low-cost microcontrollers.",
                    "content_urls": {"desktop": {"page": page}}}  # fmt: skip
        if "summary/" in url:
            raise OSError("no summary")  # -> the search snippet is used
        if "brave" in url:
            if self.fail_brave:
                raise OSError("401")
            return BRAVE
        raise AssertionError(url)


def test_wikipedia():
    found = WebLookup(get=Net()).search("esp32", "wikipedia")
    assert [f.title for f in found] == ["ESP32", "ESP8266"]
    assert found[0].text.startswith("ESP32 is a family") and found[0].source == "Wikipedia"
    assert found[1].text == "older"  # (summary failed: the snippet, tags removed)


def test_brave_when_there_is_a_key_and_its_fallback():
    net = Net()
    found = WebLookup(brave_key=lambda: "k", get=net).search("chandrayaan news")
    assert found[0].source == "Example News" and found[0].text == "Chandrayaan-4 approved"
    assert net.calls[0][2]["X-Subscription-Token"] == "k"
    assert WebLookup(get=Net()).search("x")[0].source == "Wikipedia"  # no key: Wikipedia
    broken = WebLookup(brave_key=lambda: "k", get=Net(fail_brave=True)).search("x")
    assert broken[0].source == "Wikipedia"  # Brave failed: Wikipedia instead


def test_tool_gives_her_ai_the_facts_with_sources():
    ctx = ToolContext(services={"lookup": WebLookup(get=Net())})
    r = REGISTRY.run(ToolCall("lookup", {"target": "wikipedia", "text": "esp32"}), ctx, ["lookup"])
    assert r.ok and "[Wikipedia] ESP32:" in r.facts and "say where it's from" in r.facts
    assert r.data["sources"][0] == "https://en.wikipedia.org/wiki/ESP32"


def test_offline():
    def offline(*a, **k):
        raise OSError("no internet")

    ctx = ToolContext(services={"lookup": WebLookup(get=offline)})
    r = REGISTRY.run(ToolCall("lookup", {"text": "esp32"}), ctx, ["lookup"])
    assert not r.ok and "internet" in r.say


ON = ["lookup", "web_search", "open_app", "spotify", "youtube"]
OFF = ["web_search", "open_app", "spotify", "youtube"]  # (switched off: hidden from her)


@pytest.mark.parametrize(
    ("said", "args"),
    [
        (
            "look up the chandrayaan-3 mission",
            {"target": "web", "text": "the chandrayaan 3 mission"},  # (cleaned: same search)
        ),
        ("what's the latest on seedhe maut", {"target": "web", "text": "seedhe maut"}),
        ("tell me about esp32 from wikipedia", {"target": "wikipedia", "text": "esp32"}),
        ("wikipedia pe esp32 ke baare mein batao", {"target": "wikipedia", "text": "esp32"}),
        ("search the internet for rtx 5070 price", {"target": "web", "text": "rtx 5070 price"}),
    ],
)
def test_phrases(said, args):
    call = parse_task(said, ON)
    assert call is not None and call.tool == "lookup", said
    assert call.args == args


def test_switched_off_look_up_opens_the_browser_as_before():
    call = parse_task("look up the chandrayaan-3 mission", OFF)
    assert call is not None and call.tool == "web_search"

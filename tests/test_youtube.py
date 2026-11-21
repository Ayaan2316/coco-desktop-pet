"""YouTube direct play: the top video from a (fake) results page, phrases, and fallbacks."""

import json

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext
from coco.tools.youtube import top_video


def page(*videos, ad=True):
    items = []
    if ad:
        items.append({"adSlotRenderer": {"videoId": "AAAAAAAAAAA"}})
    items += [{"reelShelfRenderer": {"items": []}}]
    items += [{"videoRenderer": {"videoId": vid, "title": {"runs": [{"text": t}]}}}
              for vid, t in videos]  # fmt: skip
    data = {"contents": {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {
        "contents": items}}]}}}  # fmt: skip
    return f"<html><script>var ytInitialData = {json.dumps(data)};</script></html>"


def test_top_video_skips_ads_and_shortens_the_title():
    html = page(("BddP6PYo2gs", "Kesariya - Brahmastra | Ranbir Kapoor | Pritam"),
                ("6mr4cYJ7yew", "Kesariya - Film Version"))  # fmt: skip
    seen = []

    def get(url, params):
        seen.append((url, params))
        return html

    assert top_video("kesariya", get) == ("BddP6PYo2gs", "Kesariya - Brahmastra")
    assert seen == [("https://www.youtube.com/results", {"search_query": "kesariya"})]
    with pytest.raises(LookupError):
        top_video("x", lambda u, p: page())
    with pytest.raises(LookupError):
        top_video("x", lambda u, p: "<html>consent page</html>")


class Launcher:
    def __init__(self):
        self.opened = []

    def open(self, target):
        self.opened.append((target.kind, target.value))


def run(text, get, lang="en"):
    launcher = Launcher()
    ctx = ToolContext(services={"launcher": launcher, "youtube_get": get})
    r = REGISTRY.run(ToolCall("youtube", {"text": text}, lang=lang), ctx, ["youtube"])
    return r, launcher.opened


def test_tool_opens_the_video():
    r, opened = run("kesariya", lambda u, p: page(("BddP6PYo2gs", "Kesariya")))
    assert opened == [("site", "https://www.youtube.com/watch?v=BddP6PYo2gs")]
    assert r.ok and r.say == "Playing Kesariya on YouTube! 🎬"
    r, _ = run("kesariya", lambda u, p: page(("BddP6PYo2gs", "Kesariya")), lang="hinglish")
    assert r.say == "YouTube pe Kesariya chala rahi hoon! 🎬" and r.speech_hi


def test_tool_falls_back_to_the_results_page():
    def offline(url, params):
        raise OSError("no internet")

    r, opened = run("lofi beats", offline)
    assert opened == [("search", "https://www.youtube.com/results?search_query=lofi+beats")]
    assert r.ok and "pick one" in r.say
    r, opened = run("", offline)
    assert not r.ok and opened == []


ALL = ["youtube", "spotify", "web_search", "open_app", "media_control"]


@pytest.mark.parametrize(
    ("said", "tool", "args"),
    [
        ("play Kesariya on YouTube", "youtube", {"text": "kesariya"}),
        ("Coco, put on some lofi on youtube", "youtube", {"text": "lofi"}),
        ("I want to watch MKBHD on youtube", "youtube", {"text": "mkbhd"}),
        ("play the kesariya video on yt", "youtube", {"text": "kesariya"}),
        ("youtube pe kesariya chalao", "youtube", {"text": "kesariya"}),
        ("kesariya youtube pe lagao", "youtube", {"text": "kesariya"}),
        ("youtube par arijit ke gaane laga do", "youtube", {"text": "arijit ke gaane"}),
        ("play Kesariya", "spotify", {"target": "play", "text": "kesariya", "amount": "auto"}),
        ("search youtube for lofi", "web_search", {"target": "youtube", "text": "lofi"}),
        ("open youtube", "open_app", {"target": "youtube"}),
    ],
)  # fmt: skip
def test_phrases(said, tool, args):
    call = parse_task(said, ALL)
    assert call is not None and call.tool == tool, said
    assert call.args == args

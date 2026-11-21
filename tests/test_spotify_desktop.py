"""Spotify without the Web API: the desktop app driven through a fake accessibility tree."""

import contextlib
import sys
import types

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.core.secrets import Secrets
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext
from coco.tools.spotify import Spotify
from coco.tools.spotify_desktop import DesktopError, Snap, SpotifyDesktop, _top_result


class Node:
    """A fake UI Automation control."""

    def __init__(self, kind, name="", kids=(), on_press=None):
        self.ControlTypeName = f"{kind}Control"
        self.Name = name
        self.kids = list(kids)
        self.parent = None
        self.on_press = on_press
        for k in self.kids:
            k.parent = self

    def GetChildren(self):  # noqa: N802 - uiautomation's names
        return self.kids

    def GetParentControl(self):  # noqa: N802
        return self.parent

    def GetInvokePattern(self):  # noqa: N802
        return types.SimpleNamespace(Invoke=self.on_press)


def snaps(*pairs):
    return [Snap(None, f"{k}Control", n) for k, n in pairs]


def test_top_result_prefers_the_top_card():
    button, label = _top_result(iter(snaps(
        ("Group", "Search for kesariya"), ("Button", "Play Sahiba"),
        ("Group", "Kesariya"), ("Button", "Play"),
    )))  # fmt: skip
    assert label == "Kesariya" and button.Name == "Play"
    button, label = _top_result(iter(snaps(("Button", "Play"), ("Button", "Play Sahiba"))))
    assert label == "Sahiba"  # no named card: the first song row
    assert _top_result(iter(snaps(("Button", "Play"), ("Button", "Pause")))) == (None, "")


class FakeSpotifyApp(SpotifyDesktop):
    """The real play() logic against a small fake window."""

    def __init__(self):
        self.opened = []
        self.playing = "Old song by Someone"
        bar = Node("Group", "Now playing bar", [Node("Group", "x")])
        self._label = bar.kids[0]
        self._sync()

        def press():
            self.playing = "Kesariya by Arijit Singh"
            self._sync()

        card = Node("Group", "Kesariya", [Node("Button", "Play", on_press=press)])
        self.results = Node("Group", "Search for old+search | Spotify",
                            [Node("Button", "Play Sahiba"), card])  # fmt: skip
        self.root = Node("Window", "Spotify", [bar, self.results])
        launcher = types.SimpleNamespace(open_uri=self._open_uri)
        super().__init__(launcher)

    def _open_uri(self, uri):
        """Spotify shows the new search's page (as Spotify names it)."""
        self.opened.append(uri)
        if uri.startswith("spotify:search:") and not getattr(self, "slow", False):
            query = uri.removeprefix("spotify:search:").replace("%20", "+")
            self.results.Name = f"Search for {query} | Spotify"

    def _sync(self):
        self._label.Name = f"Now playing: {self.playing}"

    def _window(self, start):
        return self.root


@pytest.fixture(autouse=True)
def fake_uia(monkeypatch):
    fake = types.SimpleNamespace(UIAutomationInitializerInThread=contextlib.nullcontext)
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    monkeypatch.setattr("coco.tools.spotify_desktop.time.sleep", lambda s: None)


def test_play_presses_the_top_result_and_reads_the_bar():
    app = FakeSpotifyApp()
    assert app.now_playing() == "Old song by Someone"
    assert app.play("kesariya") == "Kesariya by Arijit Singh"
    assert app.opened == ["spotify:search:kesariya"]
    app.play("arijit singh", "artist")
    assert app.opened[-1] == "spotify:search:artist%20arijit%20singh"


def test_failures(monkeypatch):
    app = FakeSpotifyApp()
    app.slow = True  # the page still shows the previous search: never press its Play
    real_find = app._find
    monkeypatch.setattr(app, "_find", lambda root, test, timeout=6.0: real_find(root, test, 0))
    with pytest.raises(LookupError):
        app.play("zzz")
    assert app.playing == "Old song by Someone"

    app.root.kids.remove(app.root.kids[0])  # no player bar: the tree is asleep
    with pytest.raises(DesktopError):
        app.play("kesariya")

    def boom(start):
        raise OSError("COM went away")

    app._window = boom
    with pytest.raises(DesktopError):
        app.play("kesariya")


def test_tool_uses_the_app_when_the_web_api_is_not_connected(tmp_path):
    app = FakeSpotifyApp()
    ctx = ToolContext(services={"spotify": Spotify(Secrets(tmp_path / "s.json")),
                                "spotify_desktop": app})  # fmt: skip
    r = REGISTRY.run(ToolCall("spotify", {"target": "play", "text": "kesariya"}), ctx, ["spotify"])
    assert r.ok and "Kesariya by Arijit Singh" in r.say
    r = REGISTRY.run(ToolCall("spotify", {"target": "now_playing"}), ctx, ["spotify"])
    assert "Kesariya by Arijit Singh" in r.say
    r = REGISTRY.run(ToolCall("spotify", {"target": "queue", "text": "perfect"}), ctx, ["spotify"])
    assert not r.ok and "queue" in r.say

    app._window = lambda start: (_ for _ in ()).throw(OSError("COM went away"))
    r = REGISTRY.run(ToolCall("spotify", {"target": "play", "text": "kesariya"}), ctx, ["spotify"])
    assert not r.ok and "Play" in r.say


def test_play_retries_when_the_page_redraws():
    app = FakeSpotifyApp()
    real_press, calls = app._press, []

    def press(snap):
        calls.append(snap)
        if len(calls) == 1:
            raise OSError("element gone")  # Spotify redrew the results under us
        real_press(snap)

    app._press = press
    assert app.play("kesariya") == "Kesariya by Arijit Singh" and len(calls) == 2

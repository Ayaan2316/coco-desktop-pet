# ruff: noqa: E501  (long fake API answers)
"""Spotify: login/refresh, what to play, devices, and phrases (fake Spotify, no network)."""

import pytest

import coco.tools  # noqa: F401 - registers the tools and their phrases
from coco.behaviour.commands import parse_command
from coco.core.secrets import Secrets
from coco.tools.intents import parse_task
from coco.tools.registry import REGISTRY, ToolCall
from coco.tools.runner import ToolContext
from coco.tools.spotify import Spotify, authorize_url, make_pkce


class FakeSpotify:
    """Answers the few Web API calls Coco makes."""

    def __init__(self, devices=True):
        self.calls = []
        self.devices = [{"id": "pc1", "type": "Computer", "is_active": False}] if devices else []
        self.token_n = 0

    def __call__(self, method, url, **kw):
        self.calls.append((method, url.split("spotify.com")[-1], kw.get("params"), kw.get("json")))
        path = url.split("spotify.com")[-1]
        if path == "/api/token":
            self.token_n += 1
            return 200, {"access_token": f"tok{self.token_n}", "expires_in": 3600,
                         "refresh_token": "refresh1"}  # fmt: skip
        if path == "/v1/me/player/devices":
            return 200, {"devices": self.devices}
        if path == "/v1/search":
            q = kw["params"]["q"].lower()
            if q == "arijit singh":
                return 200, {"artists": {"items": [{"name": "Arijit Singh", "uri": "spotify:artist:A"}]},
                             "tracks": {"items": [{"name": "Tum Hi Ho", "uri": "spotify:track:T0",
                                                   "artists": [{"name": "Arijit Singh"}]}]}}  # fmt: skip
            if "album" in kw["params"]["type"]:
                return 200, {"albums": {"items": [{"name": "Divide", "uri": "spotify:album:D",
                                                   "artists": [{"name": "Ed Sheeran"}]}]}}  # fmt: skip
            if kw["params"]["type"] == "playlist":
                return 200, {
                    "playlists": {"items": [{"name": "Lofi Beats", "uri": "spotify:playlist:L"}]}
                }
            return 200, {"artists": {"items": []},
                         "tracks": {"items": [{"name": "Kesariya", "uri": "spotify:track:K",
                                               "artists": [{"name": "Arijit Singh"}]}]}}  # fmt: skip
        if path == "/v1/me/tracks":
            return 200, {"items": [{"track": {"uri": "spotify:track:L1"}},
                                   {"track": {"uri": "spotify:track:L2"}}]}  # fmt: skip
        if path == "/v1/me/playlists":
            return 200, {"items": [{"name": "Gym", "uri": "spotify:playlist:G"}]}
        if path == "/v1/me/player/currently-playing":
            return 200, {"item": {"name": "Kesariya", "artists": [{"name": "Arijit Singh"}]}}
        return 204, None


@pytest.fixture
def sp(tmp_path):
    secrets = Secrets(tmp_path / "secrets.json")
    secrets.set("spotify.client_id", "client123")
    fake = FakeSpotify()
    clock = [1000.0]
    s = Spotify(secrets, http=fake, clock=lambda: clock[0])
    s.finish_login("code1", "verifier1")
    s.fake, s.clock_ref = fake, clock
    return s


def plays(sp):
    return [c for c in sp.fake.calls if c[1] == "/v1/me/player/play"]


def test_login_and_refresh(sp, tmp_path):
    assert sp.connected and Secrets(tmp_path / "secrets.json").get("spotify.refresh_token")
    assert sp._token() == "tok1"
    sp.clock_ref[0] += 3600  # expired -> refreshed automatically
    assert sp._token() == "tok2"
    v, c = make_pkce()
    url = authorize_url("client123", c, "state")
    assert "code_challenge_method=S256" in url and "127.0.0.1%3A8890" in url


def test_what_to_play(sp):
    assert sp.play("Kesariya") == "Kesariya by Arijit Singh"
    assert plays(sp)[-1][3] == {"uris": ["spotify:track:K"]}
    assert sp.play("arijit singh") == "Arijit Singh"  # an artist name -> the artist
    assert plays(sp)[-1][3] == {"context_uri": "spotify:artist:A"}
    sp.play("Divide", "album")
    assert plays(sp)[-1][3] == {"context_uri": "spotify:album:D"}
    assert sp.play("gym", "playlist") == "Gym"  # your own playlists first
    sp.play("", "liked")
    assert plays(sp)[-1][3] == {"uris": ["spotify:track:L1", "spotify:track:L2"]}
    assert plays(sp)[-1][2] == {"device_id": "pc1"}
    assert sp.now_playing() == "Kesariya by Arijit Singh"


def test_starts_spotify_when_no_device(tmp_path, monkeypatch):
    monkeypatch.setattr("coco.tools.spotify.time.sleep", lambda s: None)
    secrets = Secrets(tmp_path / "s.json")
    secrets.set("spotify.client_id", "c")
    fake = FakeSpotify(devices=False)
    launched = []

    def launch():
        launched.append(1)
        fake.devices = [{"id": "pc9", "type": "Computer", "is_active": False}]

    s = Spotify(secrets, http=fake, launch=launch)
    s.finish_login("code", "v")
    s.fake = fake
    s.play("Kesariya")
    assert launched == [1] and plays(s)[-1][2] == {"device_id": "pc9"}


def test_tool_and_not_connected(tmp_path, sp):
    opened = []

    class L:
        def open_uri(self, uri):
            opened.append(uri)

    secrets = Secrets(tmp_path / "empty.json")
    ctx = ToolContext(services={"spotify": Spotify(secrets, http=FakeSpotify()), "launcher": L()})
    r = REGISTRY.run(ToolCall("spotify", {"target": "play", "text": "Kesariya"}), ctx, ["spotify"])
    assert not r.ok and opened == ["spotify:search:Kesariya"] and "Connect Spotify" in r.say
    ctx = ToolContext(services={"spotify": sp})
    r = REGISTRY.run(ToolCall("spotify", {"target": "play", "text": "Kesariya"}, lang="hinglish"),
                     ctx, ["spotify"])  # fmt: skip
    assert r.say == "Kesariya by Arijit Singh baja rahi hoon! 🎵" and r.speech_hi


@pytest.mark.parametrize(
    ("said", "args"),
    [
        ("play Kesariya", {"target": "play", "text": "kesariya", "amount": "auto"}),
        ("Coco, play some lofi", {"target": "play", "text": "lofi", "amount": "playlist"}),
        ("play songs by Arijit Singh", {"target": "play", "text": "arijit singh", "amount": "artist"}),
        ("play the album Divide", {"target": "play", "text": "divide", "amount": "album"}),
        ("play my gym playlist", {"target": "play", "text": "gym", "amount": "playlist"}),
        ("play my liked songs", {"target": "play", "amount": "liked"}),
        ("Kesariya lagao", {"target": "play", "text": "kesariya", "amount": "auto"}),
        ("arijit ke gaane bajao", {"target": "play", "text": "arijit", "amount": "artist"}),
        ("add Perfect to the queue", {"target": "queue", "text": "perfect"}),
        ("what's playing?", {"target": "now_playing"}),
        ("ye kaunsa gaana hai", {"target": "now_playing"}),
        ("shuffle on", {"target": "shuffle_on"}),
    ],
)  # fmt: skip
def test_phrases(said, args):
    tools = ["spotify", "media_control", "web_search", "reminder", "open_app"]
    call = parse_task(said, tools)
    assert call is not None and call.tool == "spotify", said
    assert call.args == args


@pytest.mark.parametrize(
    ("said", "tool"),
    [
        ("play lofi on youtube", "web_search"),
        ("play music", "media_control"),
        ("5 minute ka timer laga do", "reminder"),
        ("pause the music", "media_control"),
    ],
)
def test_not_spotify(said, tool):
    tools = ["spotify", "media_control", "web_search", "reminder", "open_app"]
    assert parse_task(said, tools).tool == tool


def test_body_commands_still_win():
    assert parse_command("play with me") == "play"
    assert parse_task("mujhe ek joke sunao", ["spotify"]) is None

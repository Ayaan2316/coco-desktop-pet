"""Spotify through the Web API."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets as pysecrets
import threading
import time
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool
from coco.tools.spotify_desktop import DesktopError

log = logging.getLogger(__name__)

REDIRECT_PORT = 8890
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPES = (
    "user-read-playback-state user-modify-playback-state user-read-currently-playing "
    "user-library-read playlist-read-private playlist-read-collaborative"
)
API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"


class SpotifyError(Exception):
    pass


class NotConnected(SpotifyError):
    pass


# --- login (PKCE)
def make_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.decode().rstrip("=")


def authorize_url(client_id: str, challenge: str, state: str) -> str:
    q = {
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "code_challenge_method": "S256", "code_challenge": challenge, "scope": SCOPES,
        "state": state,
    }  # fmt: skip
    return f"{ACCOUNTS}/authorize?{urllib.parse.urlencode(q)}"


def wait_for_code(state: str, timeout: float = 180.0) -> str | None:
    """Run a one-shot local page on 127.0.0.1:8890 and wait for Spotify's redirect."""
    got: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server naming)
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = qs.get("state", [""])[0] == state and "code" in qs
            if ok:
                got["code"] = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = (
                "Spotify is connected to Coco! You can close this tab. 🐾"
                if ok
                else "Spotify login didn't work. Please try again from Coco's tray menu."
            )
            self.wfile.write(f"<h2 style='font-family:sans-serif'>{msg}</h2>".encode())

        def log_message(self, *args):  # keep the console quiet
            pass

    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
    server.timeout = 1.0
    deadline = time.monotonic() + timeout
    try:
        while "code" not in got and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    return got.get("code")


# --- the client
class Spotify:
    """Tiny Web API client. `http(method, url, **kw)` -> (status, json|None) is injectable."""

    def __init__(self, secrets, http: Callable | None = None, launch=None, clock=time.time):
        self.secrets = secrets
        self.http = http or _httpx_request
        self.launch = launch  # opens the Spotify app (if no device is running)
        self.clock = clock
        self._lock = threading.Lock()

    # ---- login state
    @property
    def client_id(self) -> str:
        return str(self.secrets.get("spotify.client_id") or "")

    @property
    def connected(self) -> bool:
        return bool(self.secrets.get("spotify.refresh_token"))

    def finish_login(self, code: str, verifier: str) -> None:
        status, data = self.http("POST", f"{ACCOUNTS}/api/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
            "client_id": self.client_id, "code_verifier": verifier,
        })  # fmt: skip
        if status != 200 or not data:
            raise SpotifyError(f"login failed ({status})")
        self._save_tokens(data)

    def _save_tokens(self, data: dict) -> None:
        self.secrets.set("spotify.access_token", data["access_token"])
        self.secrets.set("spotify.expires_at", self.clock() + int(data.get("expires_in", 3600)))
        if data.get("refresh_token"):
            self.secrets.set("spotify.refresh_token", data["refresh_token"])

    def _token(self) -> str:
        if not self.connected:
            raise NotConnected("Spotify isn't connected")
        with self._lock:
            if self.clock() > float(self.secrets.get("spotify.expires_at") or 0) - 60:
                status, data = self.http("POST", f"{ACCOUNTS}/api/token", data={
                    "grant_type": "refresh_token", "client_id": self.client_id,
                    "refresh_token": self.secrets.get("spotify.refresh_token"),
                })  # fmt: skip
                if status != 200 or not data:
                    if status in (400, 401):
                        self.secrets.set("spotify.refresh_token", None)  # log in again
                        raise NotConnected("Spotify login expired")
                    raise SpotifyError(f"token refresh failed ({status})")
                self._save_tokens(data)
            return str(self.secrets.get("spotify.access_token"))

    def api(self, method: str, path: str, **kw):
        headers = {"Authorization": f"Bearer {self._token()}"}
        status, data = self.http(method, f"{API}{path}", headers=headers, **kw)
        if status == 401:  # token went stale: refresh once
            self.secrets.set("spotify.expires_at", 0)
            headers = {"Authorization": f"Bearer {self._token()}"}
            status, data = self.http(method, f"{API}{path}", headers=headers, **kw)
        if status >= 400:
            msg = (data or {}).get("error", {}).get("message", "") if isinstance(data, dict) else ""
            raise SpotifyError(f"{method} {path}: {status} {msg}")
        return data

    # ---- devices
    def device_id(self, wait: float = 12.0) -> str:
        """The active device, or the PC's Spotify app (started if needed)."""
        devices = (self.api("GET", "/me/player/devices") or {}).get("devices", [])
        if not devices and self.launch is not None:
            self.launch()
            deadline = self.clock() + wait
            while not devices and self.clock() < deadline:
                time.sleep(1.0)
                devices = (self.api("GET", "/me/player/devices") or {}).get("devices", [])
        if not devices:
            raise SpotifyError("no Spotify device")
        active = [d for d in devices if d.get("is_active")]
        pc = [d for d in devices if d.get("type") == "Computer"]
        return (active or pc or devices)[0]["id"]

    # ---- what to play
    def find(self, query: str, kind: str = "auto") -> tuple[str, str, str] | None:
        """(kind, uri, label) for "Kesariya" / "arijit singh" / album / playlist."""
        if kind == "liked":
            return ("liked", "", "your liked songs")
        if kind == "playlist":
            mine = self.api("GET", "/me/playlists", params={"limit": 50}) or {}
            for p in mine.get("items", []):
                if p and _close(query, p.get("name", "")):
                    return ("playlist", p["uri"], p["name"])
        types = {"auto": "track,artist", "track": "track", "artist": "artist",
                 "album": "album", "playlist": "playlist"}[kind]  # fmt: skip
        data = self.api("GET", "/search", params={"q": query, "type": types, "limit": 3}) or {}
        artists = [a for a in (data.get("artists") or {}).get("items", []) if a]
        if (
            kind in ("auto", "artist")
            and artists
            and (kind == "artist" or _close(query, artists[0]["name"]))
        ):
            return ("artist", artists[0]["uri"], artists[0]["name"])
        for key, k in (("tracks", "track"), ("albums", "album"), ("playlists", "playlist")):
            items = [i for i in (data.get(key) or {}).get("items", []) if i]
            if items:
                it = items[0]
                who = ", ".join(a["name"] for a in it.get("artists", [])[:2])
                label = f"{it['name']} by {who}" if who else it["name"]
                return (k, it["uri"], label)
        return None

    def play(self, query: str, kind: str = "auto") -> str:
        hit = self.find(query, kind)
        if hit is None:
            raise LookupError(query)
        k, uri, label = hit
        device = self.device_id()
        if k == "liked":
            saved = self.api("GET", "/me/tracks", params={"limit": 50}) or {}
            uris = [i["track"]["uri"] for i in saved.get("items", []) if i.get("track")]
            if not uris:
                raise LookupError("liked songs")
            self.api("PUT", "/me/player/shuffle", params={"state": "true", "device_id": device})
            body = {"uris": uris}
        elif k == "track":
            body = {"uris": [uri]}
        else:
            body = {"context_uri": uri}
        self.api("PUT", "/me/player/play", params={"device_id": device}, json=body)
        return label

    def queue(self, query: str) -> str:
        hit = self.find(query, "track")
        if hit is None:
            raise LookupError(query)
        self.api("POST", "/me/player/queue", params={"uri": hit[1], "device_id": self.device_id()})
        return hit[2]

    def shuffle(self, on: bool) -> None:
        self.api("PUT", "/me/player/shuffle", params={"state": "true" if on else "false"})

    def now_playing(self) -> str | None:
        data = self.api("GET", "/me/player/currently-playing")
        item = (data or {}).get("item")
        if not item:
            return None
        who = ", ".join(a["name"] for a in item.get("artists", [])[:2])
        return f"{item['name']} by {who}" if who else item["name"]


def _close(a: str, b: str) -> bool:
    import difflib

    a, b = a.lower().strip(), b.lower().strip()
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85


def _httpx_request(method: str, url: str, **kw):
    import httpx

    r = httpx.request(method, url, timeout=10, **kw)
    try:
        data = r.json() if r.content else None
    except ValueError:
        data = None
    return r.status_code, data


# --- the tool
def _r(lang, en, hinglish, devanagari="", ok=True, emotion="happy") -> ToolResult:
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion)
    return ToolResult(ok=ok, say=en, emotion=emotion)


@tool(
    "spotify",
    "Spotify music (target: play | queue | shuffle_on | shuffle_off | now_playing; text: "
    "song / artist / album / playlist; amount: 'album' | 'artist' | 'playlist' | 'liked')",
    words=r"play|song|music|spotify|gaana|gana|lagao|chalao|bajao|queue|shuffle|playing|"
    r"album|playlist|artist|liked",
    label="Spotify",
)
def spotify(call: ToolCall, ctx) -> ToolResult:
    sp: Spotify = ctx.spotify
    lang = call.lang
    action = call.arg("target", "play").lower()
    query = call.arg("text")
    kind = call.arg("amount", "auto").lower() or "auto"
    kind = kind if kind in ("auto", "track", "artist", "album", "playlist", "liked") else "auto"
    if not sp.connected:
        if "spotify_desktop" in ctx.services:
            sp = ctx.spotify_desktop  # no Web API: drive the Spotify app itself
        else:
            if action in ("play", "queue") and query and "launcher" in ctx.services:
                ctx.launcher.open_uri(f"spotify:search:{urllib.parse.quote(query)}")
            return _r(lang, "Spotify isn't connected yet, so I just opened the search in "
                      "Spotify. Connect it once from my tray menu (Connect Spotify) and I'll "
                      "play it myself!",
                      "Spotify abhi connect nahi hai, toh Spotify mein search khol diya. Tray "
                      "menu se ek baar 'Connect Spotify' kar do, phir main khud bajaungi!",
                      "स्पॉटिफ़ाई अभी कनेक्ट नहीं है।", ok=False, emotion="curious")  # fmt: skip
    try:
        if action == "now_playing":
            song = sp.now_playing()
            if not song:
                return _r(lang, "Nothing's playing right now~", "Abhi kuch nahi chal raha~",
                          "अभी कुछ नहीं चल रहा।", emotion="neutral")  # fmt: skip
            return _r(lang, f"That's {song}! *tail sways*", f"Ye {song} hai! *tail sways*",
                      "ये गाना चल रहा है!", emotion="happy")  # fmt: skip
        if action in ("shuffle_on", "shuffle_off"):
            sp.shuffle(action == "shuffle_on")
            word = "on" if action == "shuffle_on" else "off"
            return _r(lang, f"Shuffle {word}~", f"Shuffle {word}~", "हो गया।")
        if not query and kind != "liked":
            return _r(lang, "What should I play? Meow?", "Kya bajaun? Meow?", "क्या बजाऊँ?",
                      ok=False, emotion="curious")  # fmt: skip
        if action == "queue":
            if not getattr(sp, "can_queue", True):
                return _r(lang, "I can't add to the queue without the Spotify Web API, sorry~ "
                          "Say 'play ...' and I'll play it now instead!",
                          "Web API ke bina queue nahi kar sakti~ 'play ...' bolo, abhi baja "
                          "deti hoon!", "क्यू नहीं कर सकती।", ok=False,
                          emotion="sad")  # fmt: skip
            label = sp.queue(query)
            return _r(lang, f"Added to the queue: {label}~", f"Queue mein daal diya: {label}~",
                      "क्यू में डाल दिया।")  # fmt: skip
        label = sp.play(query, kind)
        result = _r(lang, f"Playing {label}! 🎵 *tail sways*", f"{label} baja rahi hoon! 🎵",
                    "बजा रही हूँ!")  # fmt: skip
        result.data["played"] = label  # (she learns the names: music_words.py)
        return result
    except LookupError:
        return _r(lang, f"I couldn't find '{query}' on Spotify. Meow?",
                  f"Spotify pe '{query}' nahi mila. Meow?", "स्पॉटिफ़ाई पे नहीं मिला।",
                  ok=False, emotion="curious")  # fmt: skip
    except NotConnected:
        return _r(lang, "My Spotify login expired. Connect it again from my tray menu, please~",
                  "Spotify login expire ho gaya. Tray menu se dobara connect kar do~",
                  "स्पॉटिफ़ाई दोबारा कनेक्ट कर दो।", ok=False, emotion="sad")  # fmt: skip
    except DesktopError as exc:
        log.warning("Spotify app: %s", exc)
        return _r(lang, "Spotify's app didn't let me press Play... Meow. Try again?",
                  "Spotify app ne Play dabane nahi diya... Meow. Phir se try karein?",
                  "स्पॉटिफ़ाई ने नहीं चलाया।", ok=False, emotion="sad")  # fmt: skip
    except SpotifyError as exc:
        log.warning("Spotify: %s", exc)
        if "no Spotify device" in str(exc):
            return _r(lang, "Spotify didn't open in time. Open it once and ask me again~",
                      "Spotify time pe nahi khula. Ek baar khol do, phir bolo~",
                      "स्पॉटिफ़ाई नहीं खुला।", ok=False, emotion="sad")  # fmt: skip
        return _r(lang, "Spotify said no... Meow. (Is Premium active?)",
                  "Spotify ne mana kar diya... Meow.", "स्पॉटिफ़ाई ने मना कर दिया।",
                  ok=False, emotion="sad")  # fmt: skip


# --- phrases
def _play(m: re.Match, said: str) -> dict[str, str] | None:
    q = m.group("q").strip()
    q = re.sub(r"\s+on spotify$", "", q)
    if re.search(
        r"\b(timer|reminder|alarm|focus|note|notes|screenshot|volume|awaaz|video|"
        r"movie|game)\b",
        q,
    ):
        return None  # "timer laga do", "play the video" ... are other tools
    if re.search(r"\bon (youtube|yt)$", q) or not q:
        return None  # "play X on YouTube" is YouTube's
    if q in ("music", "some music", "song", "songs", "it", "the music", "something", "gaana"):
        return None  # "play music" = resume (media keys)
    kind = "auto"
    m2 = re.match(r"^(?:my\s+)?liked songs$|^(?:my\s+)?favou?rite songs$|^meri liked songs$", q)
    if m2:
        return {"target": "play", "amount": "liked"}
    for pattern, k in (
        (r"^(?:the\s+)?album\s+(.+)$", "album"),
        (r"^(.+?)\s+album$", "album"),
        (r"^(?:songs|music|something)\s+by\s+(.+)$", "artist"),
        (r"^(?:the\s+)?artist\s+(.+)$", "artist"),
        (r"^(.+?)'s\s+songs$", "artist"),
        (r"^(.+?)\s+ke\s+gaane$", "artist"),
        (r"^(?:my\s+)?(.+?)\s+playlist$", "playlist"),
        (r"^(?:the\s+)?playlist\s+(.+)$", "playlist"),
        (r"^some\s+(.+)$", "playlist"),  # "play some lofi" -> a lofi playlist
    ):
        mm = re.match(pattern, q)
        if mm:
            q, kind = mm.group(1).strip(), k
            break
    return {"target": "play", "text": q, "amount": kind}


intent(
    "spotify",
    r"\b(?:what'?s|what is|which song is)\s+(?:this\s+)?(?:playing|song)\b|"
    r"\bwhat song is this\b|\bye kaunsa gaana hai\b|\bkaunsa gaana chal raha\b",
    args=lambda m, s: {"target": "now_playing"},
    questions=True,
    priority=7,
)
intent(
    "spotify",
    r"\bshuffle\s+(?:on|my music)\b|\bturn on shuffle\b",
    args=lambda m, s: {"target": "shuffle_on"},
    priority=7,
)
intent(
    "spotify",
    r"\bshuffle\s+off\b|\bturn off shuffle\b",
    args=lambda m, s: {"target": "shuffle_off"},
    priority=7,
)
intent(
    "spotify",
    r"^(?:add|put)\s+(?P<q>.+?)\s+(?:to|in|on)\s+(?:the\s+|my\s+)?queue$|^queue\s+(?P<q2>.+)$",
    args=lambda m, s: {"target": "queue", "text": (m.group("q") or m.group("q2")).strip()},
    priority=6,
)
intent(
    "spotify",
    r"^(?:play|put on|start playing|bajao|chalao|lagao)\s+(?P<q>.+)$",
    r"^(?P<q>.+?)\s+(?:gaana\s+|song\s+)?(?:lagao|chalao|bajao|play karo|laga do|chala do|"
    r"baja do)$",
    args=_play,
    priority=6,
)

"""Plays the top YouTube result for a search."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from collections.abc import Callable

from coco.tools.apps import Target, search_url
from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool

log = logging.getLogger(__name__)

RESULTS = "https://www.youtube.com/results"
WATCH = "https://www.youtube.com/watch?v={id}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "en"}
_DATA = re.compile(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", re.S)
_ID = re.compile(r"^[\w-]{11}$")


def _http_get(url: str, params: dict) -> str:
    import httpx

    r = httpx.get(url, params=params, headers=_HEADERS, timeout=8, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _videos(data):
    """Every normal video on the page, in order (ads, shorts and channels use other keys)."""
    stack = [data]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if isinstance(v, dict) and _ID.match(str(v.get("videoId", ""))):
                yield v
            stack.extend(reversed(list(o.values())))
        elif isinstance(o, list):
            stack.extend(reversed(o))


def _title(video: dict) -> str:
    runs = (video.get("title") or {}).get("runs") or []
    title = "".join(r.get("text", "") for r in runs).strip()
    title = re.split(r"\s+[|]\s+", title)[0].strip()  # "Kesariya - Brahmastra | Ranbir | ..."
    return title[:70] or "that video"


def top_video(query: str, get: Callable[[str, dict], str] = _http_get) -> tuple[str, str]:
    """(video id, short title) of the first video YouTube shows for `query`."""
    page = get(RESULTS, {"search_query": query})
    m = _DATA.search(page)
    if not m:
        raise LookupError("no results data on the page")
    for video in _videos(json.loads(m.group(1))):
        return video["videoId"], _title(video)
    raise LookupError(query)


def _r(lang, en, hinglish, devanagari="", ok=True, emotion="happy") -> ToolResult:
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion)
    return ToolResult(ok=ok, say=en, emotion=emotion)


@tool(
    "youtube",
    "play a video on YouTube in the browser (text: what to play)",
    words=r"youtube|\byt\b|video|watch|play|chalao|lagao",
    label="YouTube",
)
def youtube(call: ToolCall, ctx) -> ToolResult:
    query = call.arg("text") or call.arg("target")
    if not query or query.lower() == "youtube":
        return _r(call.lang, "What should I play on YouTube? Meow?", "YouTube pe kya chalaun?",
                  "यूट्यूब पे क्या चलाऊँ?", ok=False, emotion="curious")  # fmt: skip
    get = ctx.services.get("youtube_get", _http_get)
    try:
        video_id, title = top_video(query, get)
    except Exception as exc:  # noqa: BLE001 - offline, page changed...: show the results
        log.info("YouTube top video for %r failed (%s): opening the results", query, exc)
        ctx.launcher.open(Target("search", search_url("youtube", query), query))
        return _r(call.lang, f"Here's YouTube for {query}~ pick one!",
                  f"YouTube pe {query} khol diya~ ek chun lo!", "यूट्यूब खोल दिया!")  # fmt: skip
    ctx.launcher.open(Target("site", WATCH.format(id=urllib.parse.quote(video_id)), title))
    return _r(call.lang, f"Playing {title} on YouTube! 🎬",
              f"YouTube pe {title} chala rahi hoon! 🎬", "यूट्यूब पे चला रही हूँ!")  # fmt: skip


# --- phrases
def _args(m: re.Match, said: str) -> dict[str, str] | None:
    q = (m.group("q") or "").strip()
    q = re.sub(r"^(?:some|a|the)\s+", "", q)
    q = re.sub(r"\s+(?:video|videos|song|gaana)$", "", q)
    return {"text": q} if q else None


_ON_YT = r"\s+(?:on|in)\s+(?:youtube|yt)"
_HI_PLAY = r"(?:chalao|lagao|bajao|chala do|laga do|baja do|play karo|play kar do|dikhao)"
intent(
    "youtube",
    rf"^(?:play|put on|start)\s+(?P<q>.+?){_ON_YT}$",
    r"^(?:i want to |i wanna |let'?s |i'd like to |can you )?(?:watch|listen to|see)\s+"
    rf"(?P<q>.+?){_ON_YT}$",
    rf"^(?:on\s+)?youtube\s+(?:pe|par|mein|me)\s+(?P<q>.+?)\s+{_HI_PLAY}$",
    rf"^(?P<q>.+?)\s+youtube\s+(?:pe|par|mein|me)\s+{_HI_PLAY}$",
    r"^(?:play|put on)\s+(?P<q>.+?)\s+youtube\s+(?:video|pe|par)$",
    args=_args,
    priority=7,  # beats Spotify's "play X" / "X chalao" and the web_search catch-all
)

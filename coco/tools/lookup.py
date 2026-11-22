"""Web lookups through Wikipedia and, optionally, Brave Search."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import quote

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool

log = logging.getLogger(__name__)

# Wikimedia refuses vague User-Agents (403): name the app, a reference URL and the client
# library. No personal details (your name / email are never sent).
UA = "CocoDesktopCat/1.0 (https://www.mediawiki.org/wiki/API:Etiquette; personal use) httpx"
WIKI = "https://en.wikipedia.org"
BRAVE = "https://api.search.brave.com/res/v1/web/search"


@dataclass
class Found:
    title: str
    text: str
    url: str
    source: str  # "Wikipedia" | the site's name


_client = None


def _get(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    """GET JSON through one kept-alive connection (the first lookup pays the TLS setup)."""
    global _client
    import httpx

    if _client is None:
        _client = httpx.Client(timeout=8, follow_redirects=True, headers={"User-Agent": UA})
    r = _client.get(url, params=params, headers=headers or {})
    r.raise_for_status()
    return r.json()


class WebLookup:
    def __init__(self, brave_key: Callable[[], str] = lambda: "", get: Callable = _get) -> None:
        self.brave_key = brave_key
        self.get = get

    def wikipedia(self, query: str, n: int = 2) -> list[Found]:
        data = self.get(f"{WIKI}/w/api.php", {"action": "query", "list": "search", "format": "json",
                                              "srsearch": query, "srlimit": n})  # fmt: skip
        hits = (data.get("query") or {}).get("search", [])[:n]

        def summary(hit: dict) -> dict:
            title = hit.get("title", "")
            try:
                return self.get(f"{WIKI}/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}")
            except Exception:  # noqa: BLE001 - use the search snippet instead
                return {"extract": re.sub(r"<[^>]+>", "", hit.get("snippet", ""))}

        with ThreadPoolExecutor(max_workers=max(1, len(hits))) as pool:  # both at once
            pages = list(pool.map(summary, hits))
        out = []
        for hit, page in zip(hits, pages, strict=True):
            title = hit.get("title", "")
            text = (page.get("extract") or "").strip()
            if text:
                url = ((page.get("content_urls") or {}).get("desktop") or {}).get("page", "")
                out.append(Found(title, text[:1200], url or f"{WIKI}/wiki/{quote(title)}",
                                 "Wikipedia"))  # fmt: skip
        return out

    def brave(self, query: str, n: int = 5) -> list[Found]:
        key = self.brave_key()
        if not key:
            return []
        data = self.get(BRAVE, {"q": query, "count": n},
                        {"X-Subscription-Token": key, "Accept": "application/json"})  # fmt: skip
        out = []
        for r in ((data.get("web") or {}).get("results") or [])[:n]:
            desc = re.sub(r"<[^>]+>", "", r.get("description", "")).strip()
            site = (r.get("profile") or {}).get("name") or r.get("meta_url", {}).get("hostname", "")
            if desc:
                out.append(Found(r.get("title", ""), desc[:500], r.get("url", ""), site or "web"))
        return out

    def search(self, query: str, where: str = "auto") -> list[Found]:
        if where != "wikipedia" and self.brave_key():
            try:
                found = self.brave(query)
                if found:
                    return found
            except Exception as exc:  # noqa: BLE001 - fall back to Wikipedia
                log.info("Brave search failed (%s); trying Wikipedia", exc)
        return self.wikipedia(query)


def _r(lang, en, hinglish, devanagari="", ok=True, emotion="happy") -> ToolResult:
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion)
    return ToolResult(ok=ok, say=en, emotion=emotion)


@tool(
    "lookup",
    "find something out on the internet and answer from it (target: wikipedia | web; text: "
    "what to look up). For news, recent events and facts that change, not for chat",
    words=r"look|find|latest|news|wikipedia|internet|web|search|who|what|pata|dekho|batao",
    label="Web lookups (Wikipedia / Brave)",
)
def lookup(call: ToolCall, ctx) -> ToolResult:
    query = call.arg("text").strip(" ?.!")
    if len(query) < 2:
        return _r(call.lang, "Look up what? Meow?", "Kya dhoondhun?", "क्या ढूँढूँ?", ok=False,
                  emotion="curious")  # fmt: skip
    where = "wikipedia" if call.arg("target").lower() == "wikipedia" else "auto"
    try:
        found = ctx.lookup.search(query, where)
    except Exception as exc:  # noqa: BLE001 - offline, blocked...
        log.info("lookup %r failed: %s", query, exc)
        return _r(call.lang, "I couldn't reach the internet just now. Meow...",
                  "Abhi internet tak nahi pahunch payi. Meow...", "इंटरनेट नहीं मिला।",
                  ok=False, emotion="sad")  # fmt: skip
    if not found:
        return _r(call.lang, f"I couldn't find anything about '{query}'~",
                  f"'{query}' ke baare mein kuch nahi mila~", "कुछ नहीं मिला।", ok=False,
                  emotion="curious")  # fmt: skip
    lines = [f"[{f.source}] {f.title}: {f.text}" for f in found]
    facts = (f"You looked up '{query}'. What you found:\n" + "\n".join(lines)
             + "\nAnswer from this (say where it's from, briefly). If it doesn't answer the "
             "question, say so.")  # fmt: skip
    return ToolResult(ok=True, facts=facts, emotion="curious",
                      data={"sources": [f.url for f in found]})  # fmt: skip


# --- phrases
def _q(target: str):
    def build(m, said):
        q = next((g for g in m.groups() if g), "").strip(" ?.!")
        return {"target": target, "text": q} if len(q) > 1 else None

    return build


intent(
    "lookup",
    r"^(?:tell me|what does wikipedia say|what do you find)\s+about\s+(.+?)\s+"
    r"(?:from|on|in)\s+wikipedia$",
    r"^(?:look up|search|check)\s+(.+?)\s+on\s+wikipedia$",
    r"^wikipedia\s+(?:pe|par|se|mein)\s+(.+?)\s+(?:ke baare mein\s+)?(?:batao|dekho|pata karo)$",
    args=_q("wikipedia"),
    priority=6,  # (beats web_search's "search X on wikipedia", which only opens the page)
)
intent(
    "lookup",
    r"^(?:please\s+)?(?:look up|lookup|find out)\s+(?:about\s+)?(.+)$",
    r"^(?:please\s+)?search the (?:internet|web)\s+(?:for\s+)?(.+)$",
    r"^(?:what'?s|what is) the latest (?:on|about|with|in)\s+(.+)$",
    r"^(?:any )?news (?:about|on)\s+(.+)$",
    r"^(?:internet|google)\s+(?:pe|par|se)\s+(.+?)\s+(?:ke baare mein\s+)?"
    r"(?:batao|dekho|pata karo)$",
    r"^(.+?)\s+(?:ke baare mein|ke bare me)\s+(?:pata karo|internet pe dekho)$",
    args=_q("web"),
    questions=True,
    priority=6,
)

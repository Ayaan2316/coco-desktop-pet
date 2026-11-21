"""Open apps, folders and websites, and run web searches."""

from __future__ import annotations

import difflib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool

log = logging.getLogger(__name__)

DEFAULT_APPS = {
    "vs code": "%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe",
    "visual studio code": "%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe",
    "code": "%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "my files": "explorer.exe",
    "settings": "ms-settings:",
    "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi",
    "wi-fi": "ms-settings:network-wifi",
    "display settings": "ms-settings:display",
    "sound settings": "ms-settings:sound",
    "task manager": "taskmgr.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "edge": "msedge.exe",
    "browser": "https://www.google.com",
    "spotify": "spotify:",
    "camera": "microsoft.windows.camera:",
    "downloads": "folder:Downloads",
    "downloads folder": "folder:Downloads",
    "documents": "folder:Documents",
    "pictures": "folder:Pictures",
    "desktop": "folder:Desktop",
    "screenshots": "folder:Pictures\\Coco",
    "my screenshots": "folder:Pictures\\Coco",
}  # fmt: skip
DEFAULT_SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "mail": "https://mail.google.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "whatsapp": "https://web.whatsapp.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com",
    "wikipedia": "https://en.wikipedia.org",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "translate": "https://translate.google.com",
    "google translate": "https://translate.google.com",
}  # fmt: skip
SEARCH = {
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "github": "https://github.com/search?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "amazon": "https://www.amazon.in/s?k={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "stack overflow": "https://stackoverflow.com/search?q={q}",
}  # fmt: skip
_SITE_NAME = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.(com|org|net|in|io|ai|dev|edu|gov|co)$")


@dataclass
class Target:
    kind: str  # "app" | "site" | "folder" | "shortcut" | "search"
    value: str  # what to open
    label: str  # for her reply ("VS Code")


def start_menu_dirs() -> list[Path]:
    dirs = []
    for var in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(var)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


class Launcher:
    def __init__(
        self, apps=None, sites=None, opener=None, menu_dirs=None, folders=None, start_apps=None
    ):
        self.apps = {k.lower(): v for k, v in (apps if apps is not None else DEFAULT_APPS).items()}
        self.sites = {
            k.lower(): v for k, v in (sites if sites is not None else DEFAULT_SITES).items()
        }
        self.opener = opener or os.startfile  # noqa: S606 - only allow-listed targets
        self.menu_dirs = start_menu_dirs() if menu_dirs is None else menu_dirs
        self.folders = folders  # name -> Path ("Downloads"); None = the real ones
        self._shortcuts: dict[str, Path] | None = None
        # Start apps incl. Store apps (name -> AppUserModelID); None = ask Windows (lazy)
        self._start_apps: dict[str, str] | None = start_apps

    def start_apps(self) -> dict[str, str]:
        if self._start_apps is None:
            try:
                from coco.desktop.windows import start_apps

                self._start_apps = start_apps()
            except Exception:  # noqa: BLE001 - no COM: shortcuts still work
                log.warning("Could not list Start apps", exc_info=True)
                self._start_apps = {}
        return self._start_apps

    def refresh(self) -> None:
        """Forget the app lists (a new app was installed)."""
        self._shortcuts = None
        self._start_apps = None

    # --- lookup
    def shortcuts(self) -> dict[str, Path]:
        """Start-menu shortcuts by lower-case name ("arduino ide" -> ...lnk)."""
        if self._shortcuts is None:
            found: dict[str, Path] = {}
            for d in self.menu_dirs:
                if not d.exists():
                    continue
                for lnk in d.rglob("*.lnk"):
                    name = lnk.stem.lower()
                    if "uninstall" in name or name in found:
                        continue
                    found[name] = lnk
            self._shortcuts = found
        return self._shortcuts

    def _folder(self, spec: str) -> Path:
        first, _, rest = spec.partition("\\")
        if self.folders is not None and first in self.folders:
            base = Path(self.folders[first])
        else:
            from coco.desktop.windows import local_folder

            base = local_folder(first)
        return base / rest if rest else base

    def resolve(self, name: str) -> Target:
        """What "open <name>" means. Never a raw path: unknown -> a web search."""
        key = re.sub(r"\s+", " ", name.lower().strip(" .!?"))
        key = re.sub(r"^(the|my|a|an|mera|meri|mere)\s+", "", key)
        key = re.sub(r"\s+(app|website|site|application|folder)$", "", key)
        label = name.strip(" .!?")
        if key in self.apps:
            value = self.apps[key]
            if value.startswith("folder:"):
                return Target("folder", str(self._folder(value[7:])), label)
            return Target("app", os.path.expandvars(value), label)
        # Installed apps (Start menu incl. Store apps: WhatsApp, Spotify...) beat websites
        # of the same name: "open WhatsApp" = the app you installed, not web.whatsapp.com.
        names = {n.lower(): n for n in self.start_apps()}
        hit = key if key in names else None
        if hit is None and key in self.sites:
            return Target("site", self.sites[key], label)
        if hit is None and len(key) >= 3:
            starts = [n for n in names if n.startswith(key)]
            hit = min(starts, key=len) if starts else None
        if hit is None:
            close = difflib.get_close_matches(key, list(names), n=1, cutoff=0.86)
            hit = close[0] if close else None
        if hit is not None:
            display = names[hit]
            return Target("store", f"shell:AppsFolder\\{self.start_apps()[display]}", display)
        shortcuts = self.shortcuts()
        if key in shortcuts:
            return Target("shortcut", str(shortcuts[key]), shortcuts[key].stem)
        starts = [n for n in shortcuts if n.startswith(key + " ") or n.startswith(key)]
        if len(key) >= 3 and starts:
            best = min(starts, key=len)
            return Target("shortcut", str(shortcuts[best]), shortcuts[best].stem)
        close = difflib.get_close_matches(key, list(shortcuts), n=1, cutoff=0.82)
        if close:
            return Target("shortcut", str(shortcuts[close[0]]), shortcuts[close[0]].stem)
        if _SITE_NAME.match(key):
            return Target("site", f"https://{key}", key)
        return Target("search", SEARCH["google"].format(q=quote_plus(name.strip())), label)

    def open_uri(self, uri: str) -> None:
        """An app link like spotify:search:... or whatsapp://send?... (fixed schemes only)."""
        if not re.match(r"^(spotify|whatsapp|tg|ms-settings|mailto):", uri):
            raise ValueError(f"not an allowed app link: {uri[:30]}")
        log.info("Opening app link %s", uri.split("?")[0])
        self.opener(uri)

    def open(self, target: Target) -> None:
        if target.kind == "folder":
            Path(target.value).mkdir(parents=True, exist_ok=True)
        if target.kind == "app" and os.path.isabs(target.value) and not Path(target.value).exists():
            raise FileNotFoundError(target.value)
        log.info("Opening %s %r", target.kind, target.value)
        self.opener(target.value)


def search_url(engine: str, query: str) -> str:
    engine = (engine or "google").lower().strip()
    template = SEARCH.get(engine, SEARCH["google"])
    return template.format(q=quote_plus(query.strip()))


# --- replies
def _reply(lang: str, en: str, hinglish: str, devanagari: str = "") -> ToolResult:
    if lang == "hinglish":
        return ToolResult(say=hinglish, speech_hi=devanagari)
    return ToolResult(say=en)


@tool(
    "open_app",
    "open an app, folder or website by name (target: its name, e.g. VS Code, YouTube)",
    words=r"open|launch|start|go to|take me|show|khol|chalu|chala|start kar",
    label="Open apps and websites",
)
def open_app(call: ToolCall, ctx) -> ToolResult:
    name = call.arg("target") or call.arg("text")
    if not name:
        return _reply(call.lang, "Open what? Meow?", "Kya kholun? Meow?", "क्या खोलूँ?")
    target = ctx.launcher.resolve(name)
    if target.kind == "search" and "files" in ctx.services:
        found = ctx.files.find(name)  # no such app: maybe a file ("open my IoT report")
        if found:
            from coco.tools.files import open_found

            return open_found(call, ctx, name, found)
    try:
        ctx.launcher.open(target)
    except OSError:
        log.info("could not open %s, searching instead", target)
        target = Target("search", search_url("google", name), name)
        ctx.launcher.open(target)
    if target.kind == "search":
        return _reply(call.lang, f"I couldn't find an app called {name}, so I searched for it~",
                      f"{name} naam ka app nahi mila, toh Google pe search kar diya~",
                      "वो ऐप नहीं मिला, तो गूगल पे सर्च कर दिया।")  # fmt: skip
    return _reply(call.lang, f"Opening {target.label}! Meow~", f"{target.label} khol rahi hoon!",
                  "खोल रही हूँ!")  # fmt: skip


@tool(
    "web_search",
    "search the web (target: google | youtube | github | wikipedia | amazon | maps; "
    "text: what to search for)",
    words=r"search|look up|google|find|youtube|dhoond|dhund|khoj|play",
    label="Web search",
)
def web_search(call: ToolCall, ctx) -> ToolResult:
    query = call.arg("text")
    engine = call.arg("target", "google").lower()
    if not query:
        return _reply(call.lang, "Search for what? Meow?", "Kya search karun?", "क्या सर्च करूँ?")
    engine = engine if engine in SEARCH else "google"
    ctx.launcher.open(Target("search", search_url(engine, query), query))
    where = engine.title() if engine != "youtube" else "YouTube"
    return _reply(
        call.lang,
        f"Searching {where} for {query}~",
        f"{where} pe {query} dhoond rahi hoon~",
        "ढूँढ रही हूँ!",
    )


# --- phrases
_ENGINES = r"google|youtube|github|wikipedia|amazon|maps|stack overflow"


def _search_args(m: re.Match, said: str) -> dict[str, str] | None:
    q = (m.groupdict().get("q") or "").strip()
    if not q:
        return None
    engine = (m.groupdict().get("engine") or "google").strip()
    return {"target": engine, "text": q}


def _open_args(m: re.Match, said: str) -> dict[str, str] | None:
    name = m.group("name").strip()
    if not name or len(name) > 40 or re.search(r"\b(focus|timer|reminder|note|music)\b", name):
        return None  # those are other tools ("start focus", "start a timer")
    return {"target": name}


intent(
    "web_search",
    rf"^(?:search|look up|google|find)\s+(?P<engine>{_ENGINES})\s+for\s+(?P<q>.+)$",
    rf"^(?:search|look up|find)\s+(?:for\s+)?(?P<q>.+?)\s+on\s+(?P<engine>{_ENGINES})$",
    r"^play\s+(?P<q>.+?)\s+on\s+(?P<engine>youtube)$",
    r"^(?:i want to |i wanna |let'?s |i'd like to )?(?:watch|listen to|see)\s+(?:some\s+)?"
    r"(?P<q>.+?)\s+on\s+(?P<engine>youtube)$",
    r"^(?:search|google|look up)\s+(?:for\s+)?(?P<q>.+)$",
    rf"^(?P<engine>{_ENGINES})\s+(?:pe|par|mein)\s+(?P<q>.+?)\s+"
    r"(?:search karo|search kar do|dhundo|dhoondo|dhoond do|chalao|lagao|chala do|laga do)$",
    rf"^(?P<q>.+?)\s+(?P<engine>{_ENGINES})\s+(?:pe|par)\s+"
    r"(?:search karo|dhundo|dhoondo|chalao|lagao)$",
    args=_search_args,
    priority=-10,  # catch-all: every more specific tool phrase wins
)
intent(
    "open_app",
    r"^(?:open|launch|start|go to|take me to|open up|pull up|bring up|fire up|load up)\s+"
    r"(?:the\s+|my\s+)?(?P<name>.+?)(?:\s+(?:app|software|program|for me|please))?$",
    r"^(?P<name>.+?)\s+(?:kholo|khol do|kholna|open karo|open kar do|chalu karo|chala do|"
    r"start karo|launch karo)$",
    args=_open_args,
    priority=-10,
)

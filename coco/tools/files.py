"""Find and open files by name."""

from __future__ import annotations

import difflib
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool

log = logging.getLogger(__name__)

# Opened with their normal app. Anything else is only shown in its folder.
SAFE_TO_OPEN = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".ppt", ".pptx", ".odp",
    ".xls", ".xlsx", ".ods", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".svg", ".heic", ".mp3", ".wav", ".m4a", ".flac", ".mp4", ".mkv", ".mov", ".avi",
    ".webm", ".zip", ".rar", ".7z", ".ino", ".c", ".cpp", ".h", ".hpp", ".ipynb", ".json",
    ".yaml", ".yml", ".xml", ".html", ".htm", ".tex", ".kicad_pro", ".kicad_sch",
    ".kicad_pcb", ".asc", ".m", ".slx", ".v", ".vhd", ".sv", ".log",
}  # fmt: skip
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env", "site-packages",
    "appdata", "$recycle.bin", ".idea", ".vscode", "build", "dist", ".cache", ".gradle",
}  # fmt: skip
_STOP = {
    "my", "the", "a", "an", "file", "files", "document", "doc", "folder", "where", "is",
    "open", "find", "please", "mera", "meri", "mere", "wala", "wali", "of", "for", "pls",
    "that", "this", "kholo", "dhundo", "kahan", "hai", "it", "one", "latest", "last",
}  # fmt: skip


@dataclass
class Found:
    path: Path
    score: float
    mtime: float

    @property
    def safe(self) -> bool:
        return self.path.suffix.lower() in SAFE_TO_OPEN


def _tokens(text: str) -> list[str]:
    # camelCase -> camel Case, but keep "IoT" / "PCB" together
    text = re.sub(r"([a-z])([A-Z][a-z])", r"\1 \2", text)
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def query_words(query: str) -> list[str]:
    return [w for w in _tokens(query) if w not in _STOP]


def name_score(words: list[str], filename: str) -> float:
    """Share of your words found in the file name (prefix / fuzzy match per word)."""
    if not words:
        return 0.0
    toks = _tokens(Path(filename).stem)
    joined = "".join(toks)
    hits = 0.0
    for w in words:
        if any(t == w or (len(w) >= 3 and t.startswith(w)) for t in toks):
            hits += 1
        elif len(w) >= 4 and w in joined:
            hits += 0.9
        elif difflib.get_close_matches(w, toks, n=1, cutoff=0.8):
            hits += 0.7
    return hits / len(words)


class FileFinder:
    def __init__(self, roots: list[Path] | None = None, max_depth: int = 5,
                 budget_s: float = 3.0) -> None:  # fmt: skip
        self._roots = roots
        self.max_depth = max_depth
        self.budget_s = budget_s

    def roots(self) -> list[Path]:
        if self._roots is not None:
            return self._roots
        from coco.desktop.windows import known_folder, local_folder

        roots: list[Path] = []
        for name in ("Desktop", "Documents", "Downloads", "Pictures"):
            for p in (local_folder(name), known_folder(name)):
                if p.exists() and p not in roots:
                    roots.append(p)
        return roots

    def find(self, query: str, limit: int = 3) -> list[Found]:
        words = query_words(query)
        if not words:
            return []
        deadline = time.monotonic() + self.budget_s
        found: list[Found] = []
        seen: set[str] = set()
        for root in self.roots():
            stack = [(root, 0)]
            while stack and time.monotonic() < deadline:
                folder, depth = stack.pop()
                try:
                    entries = list(os.scandir(folder))
                except OSError:
                    continue
                for e in entries:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < self.max_depth and e.name.lower() not in _SKIP_DIRS:
                                stack.append((Path(e.path), depth + 1))
                            continue
                    except OSError:
                        continue
                    score = name_score(words, e.name)
                    if score >= 0.67 and e.path.lower() not in seen:
                        seen.add(e.path.lower())
                        try:
                            mtime = e.stat().st_mtime
                        except OSError:
                            mtime = 0.0
                        found.append(Found(Path(e.path), score, mtime))
        found.sort(key=lambda f: (-round(f.score, 2), not f.safe, -f.mtime))
        return found[:limit]


# --- opening
def _r(lang, en, hinglish, devanagari="", ok=True, emotion="happy") -> ToolResult:
    if lang == "hinglish":
        return ToolResult(ok=ok, say=hinglish, speech_hi=devanagari, emotion=emotion)
    return ToolResult(ok=ok, say=en, emotion=emotion)


def _open_one(call: ToolCall, ctx, f: Found, want_folder: bool) -> ToolResult:
    lang = call.lang
    if want_folder or not f.safe:
        ctx.show_in_folder(f.path)
        if not f.safe and not want_folder:  # a program / script: never run
            return _r(lang, f"Found {f.path.name}. I don't run programs, so here's its folder~",
                      f"{f.path.name} mil gaya. Programs main nahi chalati, folder khol diya~",
                      "फ़ोल्डर खोल दिया।")  # fmt: skip
        return _r(lang, f"Here's the folder with {f.path.name}~",
                  f"{f.path.name} ka folder khol diya~", "फ़ोल्डर खोल दिया।")  # fmt: skip
    ctx.open_file(f.path)
    return _r(lang, f"Opening {f.path.name}! Meow~", f"{f.path.name} khol rahi hoon!",
              "खोल रही हूँ!")  # fmt: skip


def open_found(call: ToolCall, ctx, query: str, found: list[Found], want_folder=False):
    """One clear winner -> open it; several close ones -> buttons to pick."""
    best = found[0]
    clear = len(found) == 1 or best.score - found[1].score >= 0.2
    if clear or "ask" not in ctx.services:
        return _open_one(call, ctx, best, want_folder)
    lang = call.lang
    question = (f"Which one? I found {len(found)}:" if lang != "hinglish"
                else f"Kaunsi wali? {len(found)} mili:")  # fmt: skip
    choices = [(str(i), f.path.name[:38]) for i, f in enumerate(found)]

    def picked(key: str) -> None:
        if key.isdigit():
            ctx.report(call, _open_one(call, ctx, found[int(key)], want_folder))

    ctx.ask(call, question, choices, picked)
    return None  # reports after you pick


@tool(
    "open_file",
    "find and open one of their files by name (target: words from the file name; "
    "text: 'folder' to open its folder instead)",
    words=r"file|document|doc|pdf|report|resume|cv|notes|slides|ppt|sheet|assignment|"
    r"project|photo|picture|video|folder|open|find|where|khol|dhund",
    label="Open your files",
)
def open_file(call: ToolCall, ctx) -> ToolResult | None:
    query = call.arg("target") or call.arg("text")
    want_folder = call.arg("text").lower() == "folder" or bool(
        re.search(r"\bfolder\b", call.said or "", re.IGNORECASE))  # fmt: skip
    found = ctx.files.find(query)
    if not found:
        return _r(call.lang, f"I couldn't find a file called '{query}'. Meow?",
                  f"'{query}' naam ki koi file nahi mili. Meow?", "ऐसी कोई फ़ाइल नहीं मिली।",
                  ok=False, emotion="curious")  # fmt: skip
    return open_found(call, ctx, query, found, want_folder)


# --- phrases
def _query(m: re.Match, said: str) -> dict[str, str] | None:
    q = m.group("q").strip()
    return {"target": q} if q and query_words(q) else None


_KIND = (r"(?:file|document|doc|pdf|report|resume|cv|notes|slides|presentation|ppt|sheet|"
         r"spreadsheet|assignment|photo|picture|video)")  # fmt: skip
intent(
    "open_file",
    r"^(?:open|show me)\s+the folder (?:where|with|of|that has)\s+(?:my\s+|the\s+)?(?P<q>.+?)"
    r"(?:\s+is| are)?$",
    r"^(?:find|search for|locate|where'?s|where is)\s+(?:my\s+|the\s+)?(?P<q>.+)$",
    rf"^(?:open|show me)\s+(?:my\s+|the\s+)?(?P<q>.*\b{_KIND}s?\b.*)$",
    r"^(?:meri|mera|mere)\s+(?P<q>.+?)\s+(?:kholo|khol do|dikhao|dhundo|kahan hai)$",
    rf"^(?P<q>.+?\b{_KIND}s?\b.*?)\s+(?:kholo|khol do|dikhao|dhundo|kahan hai)$",
    args=_query,
    questions=True,
    priority=-5,  # after the specific tools, before the "open X" catch-all
)

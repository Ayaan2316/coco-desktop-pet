"""Controls the Spotify desktop app through Windows accessibility."""

from __future__ import annotations

import functools
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass

log = logging.getLogger(__name__)

SPOTIFY_EXES = ("Spotify.exe",)


class DesktopError(RuntimeError):
    """Windows accessibility failed while driving the Spotify app."""


def _guarded(fn):
    """LookupError (not found) passes through; any other accessibility failure becomes
    DesktopError, so the tool can say something friendly."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LookupError:
            raise
        except Exception as exc:  # noqa: BLE001 - COM / UIA errors come in many shapes
            raise DesktopError(f"{type(exc).__name__}: {exc}") from exc

    return wrapper


@dataclass
class Snap:
    """A control with its type and name read once (reading them later can fail)."""

    control: object
    ControlTypeName: str  # noqa: N815 - same names as uiautomation's controls
    Name: str


class SpotifyDesktop:
    def __init__(self, launcher, wait: float = 12.0) -> None:
        self.launcher = launcher
        self.wait = wait

    connected = True  # (same shape as the Web API client: always "ready")
    can_queue = False  # Spotify's app has no accessible "add to queue" by search

    # --- helpers
    def _window(self, start: bool):
        import uiautomation as auto

        from coco.desktop import windows as win

        hwnd = win.find_app_window(SPOTIFY_EXES, "Spotify")
        if hwnd is None and start:
            self.launcher.open(self.launcher.resolve("spotify"))
            deadline = time.monotonic() + self.wait
            while hwnd is None and time.monotonic() < deadline:
                time.sleep(0.5)
                hwnd = win.find_app_window(SPOTIFY_EXES, "Spotify")
        return auto.ControlFromHandle(hwnd) if hwnd else None

    @staticmethod
    def _walk(root, max_depth: int = 60):
        """Controls under root, depth first, as Snaps (type + name read once). Spotify
        redraws all the time: anything that vanishes mid-walk is skipped."""
        stack = [(root, 0)]
        while stack:
            node, depth = stack.pop()
            try:
                snap = Snap(node, node.ControlTypeName, node.Name or "")
                kids = node.GetChildren() if depth < max_depth else []
            except Exception:  # noqa: BLE001 - the element vanished: skip it
                continue
            yield snap
            stack.extend((k, depth + 1) for k in reversed(kids))

    def _controls(self, root, max_depth: int = 60) -> list:
        return list(self._walk(root, max_depth))

    def _find(self, root, test, timeout: float = 6.0):
        """Walk until `test(control)` matches, stopping at the first match (Chromium
        fills its tree in lazily, so keep trying until `timeout`)."""
        deadline = time.monotonic() + timeout
        while True:
            for snap in self._walk(root):
                try:
                    if test(snap):
                        return snap
                except Exception:  # noqa: BLE001 - a control vanished mid-walk
                    continue
            if time.monotonic() > deadline:
                return None
            time.sleep(0.3)

    @staticmethod
    def _press(snap) -> None:
        button = snap.control
        try:
            button.GetInvokePattern().Invoke()
        except Exception:  # noqa: BLE001 - no Invoke: a real click
            button.Click(simulateMove=False)

    def _ready(self, root, timeout: float = 20.0) -> None:
        """Wait for the player bar: Chromium switches its accessibility tree off when
        nobody has used it for a while, and it takes a few seconds to come back."""
        self._bar = None
        found = self._find(root, lambda c: c.ControlTypeName == "GroupControl"
                           and c.Name == "Now playing bar", timeout=timeout)  # fmt: skip
        if found is None:
            raise DesktopError("Spotify's accessibility tree didn't wake up")
        self._bar, self._bar_root = found.control, root

    def _now_playing(self, root) -> str | None:
        """Reads the player bar. The bar itself is found once and remembered (fast)."""
        bar = getattr(self, "_bar", None)
        if bar is None or getattr(self, "_bar_root", None) is not root:
            found = self._find(root, lambda c: c.ControlTypeName == "GroupControl"
                               and c.Name == "Now playing bar", timeout=1.0)  # fmt: skip
            self._bar, self._bar_root = (found.control if found else None), root
            bar = self._bar
        if bar is None:
            return None
        for snap in self._walk(bar, 3):
            if snap.Name.startswith("Now playing: "):
                return snap.Name.removeprefix("Now playing: ")
        return None

    # --- API
    @_guarded
    def now_playing(self) -> str | None:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread():
            root = self._window(start=False)
            return self._now_playing(root) if root else None

    @_guarded
    def play(self, query: str, kind: str = "auto") -> str:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread():
            if kind == "liked":
                uri = "spotify:collection:tracks"

                def locate(root):
                    button = self._find(
                        root,
                        lambda c: (
                            c.ControlTypeName == "ButtonControl"
                            and c.Name in ("Play Liked Songs", "Play")
                            and "Player controls" not in _ancestors(c.control)
                        ),
                    )
                    return button, "your liked songs"
            else:
                search = {"artist": f"artist {query}", "album": f"album {query}",
                          "playlist": f"{query} playlist"}.get(kind, query)  # fmt: skip
                uri = f"spotify:search:{urllib.parse.quote(search)}"
                # this search's results ("Search for tum+hi+ho | Spotify"), never the
                # page of an earlier search that hasn't been replaced yet
                want = "search for " + _norm(search)

                def locate(root):
                    results = self._find(root, lambda c: c.ControlTypeName == "GroupControl"
                                         and _norm(c.Name).startswith(want))  # fmt: skip
                    if results is None:
                        return None, ""
                    return _top_result(self._walk(results.control, 40))

            self.launcher.open_uri(uri)
            root = self._window(start=True)
            if root is None:
                raise LookupError("spotify")
            self._ready(root)
            before = self._now_playing(root)
            for attempt in range(3):
                button, label = locate(root)
                if button is None:
                    raise LookupError(query)
                try:
                    self._press(button)
                    break
                except Exception:  # noqa: BLE001 - the page redrew under us: find it again
                    if attempt == 2:
                        raise
                    log.info("Spotify redrew the page before Play; trying again")
                    time.sleep(0.5)
            for _ in range(8):  # confirm from the player bar
                time.sleep(0.5)
                now = self._now_playing(root)
                if now and now != before:
                    return now
            return label or query

    @_guarded
    def shuffle(self, on: bool) -> None:
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread():
            root = self._window(start=False)
            if root is None:
                raise LookupError("spotify")
            want = "Enable Shuffle" if on else "Disable Shuffle"
            button = self._find(root, lambda c: c.ControlTypeName == "ButtonControl"
                                and c.Name.startswith(want), timeout=2.0)  # fmt: skip
            if button is not None:
                self._press(button)

    def queue(self, query: str) -> str:
        raise LookupError("queue needs the Web API")


def _norm(text: str) -> str:
    """'Search for tum+hi+ho | Spotify' -> 'search for tum hi ho spotify'."""
    return " ".join(re.findall(r"\w+", (text or "").lower()))


def _ancestors(control) -> str:
    names, p = [], control
    for _ in range(6):
        p = p.GetParentControl()
        if p is None:
            break
        names.append(p.Name or "")
    return " | ".join(names)


def _top_result(controls) -> tuple[object | None, str]:
    """The search page's top result card ("Play" inside a named group), else the first
    "Play <song>" row. One pass, so `controls` can be a lazy walk that stops early."""
    current_group = ""
    fallback = None
    for c in controls:
        kind = c.ControlTypeName
        if kind == "GroupControl" and c.Name:
            current_group = c.Name
        elif kind == "ButtonControl":
            if c.Name == "Play" and current_group:
                return c, current_group
            if fallback is None and (c.Name or "").startswith("Play ") and len(c.Name) > 5:
                fallback = c
    if fallback is not None:
        return fallback, fallback.Name.removeprefix("Play ")
    return None, ""

"""Tracks the app in front and screen time, and decides when to suggest a break."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from datetime import datetime

ACTIVE_IDLE = 120.0  # no input for longer: not counted as screen time
BREAK_IDLE = 300.0  # away this long = a real break (the session starts again)

APPS: dict[str, tuple[str, str]] = {
    # program -> (what it is, how she names it)
    "code.exe": ("coding", "VS Code"), "pycharm64.exe": ("coding", "PyCharm"),
    "idea64.exe": ("coding", "IntelliJ"), "devenv.exe": ("coding", "Visual Studio"),
    "arduino ide.exe": ("coding", "Arduino IDE"), "cursor.exe": ("coding", "Cursor"),
    "matlab.exe": ("coding", "MATLAB"), "vivado.exe": ("coding", "Vivado"),
    "chrome.exe": ("browsing", "Chrome"), "msedge.exe": ("browsing", "Edge"),
    "firefox.exe": ("browsing", "Firefox"), "brave.exe": ("browsing", "Brave"),
    "winword.exe": ("writing", "Word"), "excel.exe": ("working", "Excel"),
    "powerpnt.exe": ("working", "PowerPoint"), "notepad.exe": ("writing", "Notepad"),
    "onenote.exe": ("writing", "OneNote"), "obsidian.exe": ("writing", "Obsidian"),
    "acrord32.exe": ("reading", "a PDF"), "sumatrapdf.exe": ("reading", "a PDF"),
    "blender.exe": ("creating", "Blender"), "photoshop.exe": ("creating", "Photoshop"),
    "figma.exe": ("creating", "Figma"),
    "spotify.exe": ("music", "Spotify"),
    "vlc.exe": ("video", "VLC"), "netflix.exe": ("video", "Netflix"),
    "zoom.exe": ("call", "Zoom"), "ms-teams.exe": ("call", "Teams"), "teams.exe": ("call", "Teams"),
    "discord.exe": ("chatting", "Discord"), "whatsapp.exe": ("chatting", "WhatsApp"),
    "telegram.exe": ("chatting", "Telegram"),
    "steam.exe": ("game", "Steam"), "valorant.exe": ("game", "Valorant"),
    "valorant-win64-shipping.exe": ("game", "Valorant"), "riotclientux.exe": ("game", "Riot"),
    "epicgameslauncher.exe": ("game", "Epic Games"), "minecraft.exe": ("game", "Minecraft"),
    "explorer.exe": ("files", "File Explorer"),
}  # fmt: skip
# never interrupted by a break reminder (she waits until you're out of it)
DONT_INTERRUPT = {"game", "video", "call"}


def describe_minutes(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60} min" if m % 60 else f"{m // 60} h"


class ScreenTime:
    def __init__(
        self,
        break_after: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.break_after = break_after  # 0 = no break reminders
        self.clock = clock
        self.session = 0.0  # s at the screen since the last real break
        self.today = 0.0  # s at the screen today
        self.app = ""
        self._day = datetime.fromtimestamp(clock()).date()
        self._last: float | None = None
        self._asked = False  # (once per session)
        self._snoozed_until = 0.0
        self._skip_day = None

    @property
    def kind(self) -> str:
        return APPS.get(self.app, ("", ""))[0]

    @property
    def label(self) -> str:
        return APPS.get(self.app, ("", self.app.removesuffix(".exe").title()))[1]

    def update(self, now: float, idle: float, app: str = "") -> str | None:
        day = datetime.fromtimestamp(now).date()
        if day != self._day:
            self._day, self.today = day, 0.0
        gap = 0.0 if self._last is None else now - self._last
        dt = gap if 0 < gap <= 5.0 else 0.0  # (a long gap: asleep / Coco closed, not screen time)
        self._last = now
        if app:
            self.app = app.lower()
        if idle >= BREAK_IDLE:  # away: that's a break
            if self.session:
                self.session, self._asked = 0.0, False
            return None
        if idle < ACTIVE_IDLE:
            self.session += dt
            self.today += dt
        if (
            self.break_after > 0
            and self.session >= self.break_after
            and not self._asked
            and now >= self._snoozed_until
            and self._skip_day != day
            and self.kind not in DONT_INTERRUPT
        ):
            self._asked = True
            return "break_due"
        return None

    # --- today's total
    def save(self, path) -> None:
        """Keep today's minutes across restarts (a tiny JSON next to her other data)."""
        import json

        data = {"day": str(self._day), "today": round(self.today)}
        with contextlib.suppress(OSError):
            path.write_text(json.dumps(data), encoding="utf-8")

    def load(self, path) -> None:
        import json

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("day") == str(self._day):
            self.today = float(data.get("today", 0))

    def snooze(self, seconds: float = 900.0) -> None:
        self._snoozed_until = self.clock() + seconds
        self._asked = False

    def skip_today(self) -> None:
        self._skip_day = datetime.fromtimestamp(self.clock()).date()

    def took_break(self) -> None:
        """You said you'd take one: the clock starts again even before you walk away."""
        self.session, self._asked = 0.0, False

    def sentence(self) -> str:
        """One line for her prompt (empty when there's nothing worth saying)."""
        parts = []
        if self.kind:
            doing = {"coding": "coding", "browsing": "browsing", "writing": "writing",
                     "working": "working", "reading": "reading", "creating": "creating",
                     "music": "listening to music", "video": "watching a video",
                     "call": "on a call", "chatting": "chatting", "game": "playing",
                     "files": "looking at files"}[self.kind]  # fmt: skip
            parts.append(f"They're {doing} ({self.label})")
        elif self.app and self.app not in ("explorer.exe", "searchhost.exe"):
            parts.append(f"They're using {self.label}")  # an app she has no category for
        if self.session >= 30 * 60:
            parts.append(f"{describe_minutes(self.session)} at the screen without a break")
        return (", ".join(parts) + ".") if parts else ""

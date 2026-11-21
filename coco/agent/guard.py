"""Rules for what the agent may and may not touch."""

from __future__ import annotations

import re
from dataclasses import dataclass

from coco.agent.ui_tree import UiItem, UiView

DEFAULT_EXCLUDED = [
    "bank", "sbi", "hdfc", "icici", "axis", "kotak", "paytm", "phonepe", "google pay", "gpay",
    "bhim", "upi", "1password", "bitwarden", "keepass", "lastpass", "dashlane",
    "authenticator", "wallet", "binance", "coinbase", "wazirx", "zerodha", "kite", "groww",
    "upstox", "credential manager",
]  # fmt: skip

TERMINAL_APPS = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe",
    "openconsole.exe", "bash.exe", "wsl.exe", "mintty.exe", "regedit.exe", "mmc.exe",
}  # fmt: skip
TERMINAL_WORDS = re.compile(
    r"\bterminal\b|command prompt|powershell|\bcmd\b|\bconsole\b|^run$", re.IGNORECASE
)
SECRET_FIELD = re.compile(
    r"password|passcode|pass\s*word|\bpin\b|\botp\b|one[- ]time|verification code|cvv|"
    r"\bcvc\b|card number|security code|\bupi\b|mpin|secret|recovery (?:code|key)",
    re.IGNORECASE,
)
RISKY = re.compile(
    r"\b(send|delete|remove|erase|trash|buy|pay|payment|purchase|order|checkout|check out|"
    r"place order|submit|install|uninstall|update now|sign out|log ?out|logout|close|quit|exit|"
    r"discard|don'?t save|format|reset|restart|shut ?down|empty|permanently|transfer|"
    r"unsubscribe|post|publish|share|reply|forward|block|report|accept|agree|allow|"
    r"overwrite|replace)\b",
    re.IGNORECASE,
)
MESSAGE_BOX = re.compile(r"message|chat|reply|comment|post|tweet|compose|caption|write a",
                         re.IGNORECASE)  # fmt: skip
RISKY_KEYS = {"alt+f4", "ctrl+w", "ctrl+shift+w", "ctrl+q", "delete", "shift+delete",
              "ctrl+enter", "ctrl+shift+delete", "ctrl+d"}  # fmt: skip
SETTINGS_APPS = {"systemsettings.exe"}
CHANGES_SETTING = ("CheckBox", "RadioButton", "ComboBox", "Slider", "Spinner")


@dataclass
class Verdict:
    kind: str  # "ok" | "confirm" | "refuse"
    why: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


OK = Verdict("ok")


class Guard:
    def __init__(self, excluded: list[str] | None = None) -> None:
        self.excluded = [e.lower().strip() for e in (excluded or DEFAULT_EXCLUDED) if e.strip()]

    def check_app(self, view: UiView) -> str | None:
        app, title = view.app.lower(), view.title.lower()
        if app in TERMINAL_APPS or TERMINAL_WORDS.search(title) or title == "run":
            return "I never touch terminals or command prompts (so I never run commands)."
        for word in self.excluded:
            if word and (word in app or re.search(rf"\b{re.escape(word)}\b", title)):
                return f'That app is on my "don\'t touch" list ({word}).'
        return None

    def check(self, action: dict, item: UiItem | None, view: UiView, goal: str = "") -> Verdict:
        kind = action.get("action", "")
        refused = self.check_app(view)
        if refused and kind not in ("done", "give_up", "ask", "wait"):
            return Verdict("refuse", refused)
        if kind in ("done", "give_up", "ask", "wait", "scroll"):
            return OK
        if kind == "open_app":
            name = str(action.get("text", ""))
            if TERMINAL_WORDS.search(name) or any(e in name.lower() for e in self.excluded):
                return Verdict("refuse", f"I won't open {name}.")
            return OK
        label = f"{item.name} {item.value}" if item else ""
        # (opening a terminal panel is fine; typing or pressing keys in one never is)
        if kind == "type":
            if item is None:
                item = view.focused()
            if item is not None and TERMINAL_WORDS.search(f"{item.name} {item.context}"):
                return Verdict("refuse", "That's a terminal: I never type commands.")
            if item is not None and (item.password or SECRET_FIELD.search(item.name)):
                return Verdict("refuse", "That's a password / PIN / OTP box: you type those.")
            if SECRET_FIELD.search(str(action.get("text", ""))):
                return Verdict("refuse", "I don't type secrets like passwords or OTPs.")
            return OK
        if kind == "keys":
            keys = str(action.get("text", "")).lower().replace(" ", "")
            focus = item or view.focused()
            if focus is not None and TERMINAL_WORDS.search(f"{focus.name} {focus.context}"):
                return Verdict("refuse", "That's a terminal: I never type commands.")
            if keys in RISKY_KEYS:
                return Verdict("confirm", f"press {keys} (it can close or delete things)")
            if keys == "enter" and focus is not None and MESSAGE_BOX.search(focus.name):
                return Verdict("confirm", f'press Enter in "{focus.name}" (that sends it)')
            if "win" in keys.split("+") or keys in ("alt+tab", "ctrl+alt+delete"):
                return Verdict("refuse", "I only use keys inside the app.")
            return OK
        if kind == "click" and item is not None:
            if RISKY.search(label):
                return Verdict("confirm", f'press "{item.name}"')
            in_settings = view.app in SETTINGS_APPS or (
                view.app == "applicationframehost.exe" and view.title.strip() == "Settings"
            )  # (Windows Settings runs inside ApplicationFrameHost)
            if in_settings and (
                item.kind in CHANGES_SETTING
                or item.checked is not None
                or "toggle" in item.patterns
            ):
                return Verdict("confirm", f'change "{item.name}" in Windows Settings')
            return OK
        return OK

    @staticmethod
    def goal_is_risky(goal: str) -> bool:
        return bool(RISKY.search(goal))

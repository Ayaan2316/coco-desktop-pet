"""Lock, screen off, sleep, restart and shut down."""

from __future__ import annotations

import logging
import subprocess

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool

log = logging.getLogger(__name__)

DELAY = 60  # s before a restart / shutdown, so you can still cancel it
RISKY = {"sleep", "restart", "shutdown"}


class Power:
    """The real Windows calls (tests pass a fake)."""

    def lock(self) -> None:
        import ctypes

        ctypes.windll.user32.LockWorkStation()

    def screen_off(self) -> None:
        import ctypes

        # WM_SYSCOMMAND / SC_MONITORPOWER / 2 = off (posted: never blocks on a hung window)
        ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)

    def sleep(self) -> None:
        import ctypes

        ctypes.windll.powrprof.SetSuspendState(False, True, False)

    def _shutdown(self, *args: str) -> None:
        subprocess.run(["shutdown", *args], check=False, capture_output=True,  # noqa: S607
                       creationflags=0x08000000)  # fmt: skip

    def restart(self) -> None:
        self._shutdown("/r", "/t", str(DELAY), "/c", "Coco: restarting in 60 s. Say cancel.")

    def shutdown(self) -> None:
        self._shutdown("/s", "/t", str(DELAY), "/c", "Coco: shutting down in 60 s. Say cancel.")

    def cancel(self) -> None:
        self._shutdown("/a")


SAY = {
    "lock": ("Locked! See you soon~ 🔒", "Lock kar diya! Jaldi aana~ 🔒", "लॉक कर दिया!"),
    "screen_off": (
        "Screen off~ Move the mouse to wake it.",
        "Screen band~ mouse hilao toh khul jayegi.",
        "स्क्रीन बंद।",
    ),  # fmt: skip
    "sleep": ("Night night, laptop~ 💤", "Laptop ko sula diya~ 💤", "लैपटॉप सो गया।"),
    "restart": (
        "Restarting in 60 s~ Say 'cancel the restart' to stop it.",
        "60 second mein restart~ 'restart cancel karo' bolo rokne ke liye.",
        "साठ सेकंड में रीस्टार्ट।",
    ),  # fmt: skip
    "shutdown": (
        "Shutting down in 60 s~ Say 'cancel the shutdown' to stop it.",
        "60 second mein band ho jayega~ 'shutdown cancel karo' bolo rokne ke liye.",
        "साठ सेकंड में बंद।",
    ),  # fmt: skip
    "cancel": (
        "Cancelled! Staying on~ *tail swish*",
        "Cancel kar diya! Laptop on rahega~",
        "रद्द कर दिया!",
    ),  # fmt: skip
}
ASK = {"sleep": "put the laptop to sleep", "restart": "restart the laptop (in 60 s)",
       "shutdown": "shut down the laptop (in 60 s)"}  # fmt: skip


def _confirm(call: ToolCall) -> str:
    what = ASK.get(call.arg("target"), call.arg("target"))
    return f"Pakka {what}?" if call.lang == "hinglish" else f"Sure? I'll {what}."


@tool(
    "power",
    "the laptop's power (target: lock | screen_off | sleep | restart | shutdown | cancel)",
    words=r"lock|screen|display|monitor|sleep|sula|restart|reboot|shut|band|power|cancel|off",
    label="Lock, sleep, restart, shut down",
    confirm_if=lambda c: c.arg("target") in RISKY,
    confirm_text=_confirm,
)
def power(call: ToolCall, ctx) -> ToolResult:
    action = call.arg("target").lower()
    if action not in SAY:
        return ToolResult(ok=False, say="Lock, screen off, sleep, restart or shut down?",
                          emotion="curious")  # fmt: skip
    getattr(ctx.power, action)()
    log.info("power: %s", action)
    en, hi, dev = SAY[action]
    if call.lang == "hinglish":
        return ToolResult(ok=True, say=hi, speech_hi=dev, emotion="happy")
    return ToolResult(ok=True, say=en, emotion="happy")


# --- phrases
_DEVICE = r"(?:the\s+|my\s+)?(?:laptop|pc|computer|system)"


def _p(target):
    return lambda m, s: {"target": target}


_DO = r"(?:karo|kar do)"
PHRASES = {
    "lock": [
        rf"^(?:please\s+)?lock\s+{_DEVICE}$",
        r"^(?:please\s+)?lock (?:the )?screen$",
        rf"^(?:laptop|pc|screen) lock {_DO}$",
    ],
    "screen_off": [
        r"^(?:please\s+)?turn off (?:the |my )?(?:screen|display|monitor)$",
        rf"^(?:screen|display) (?:band|off) {_DO}$",
    ],
    "sleep": [
        rf"^(?:please\s+)?put {_DEVICE} to sleep$",
        rf"^(?:please\s+)?sleep {_DEVICE}$",
        rf"^(?:laptop|pc) (?:ko )?(?:sula do|sleep {_DO})$",
    ],
    "restart": [
        rf"^(?:please\s+)?(?:restart|reboot) {_DEVICE}$",
        r"^(?:please\s+)?restart$",
        rf"^(?:laptop|pc) restart {_DO}$",
    ],
    "shutdown": [
        rf"^(?:please\s+)?(?:shut down|shutdown|switch off|turn off) {_DEVICE}$",
        rf"^(?:laptop|pc) (?:band|shutdown) {_DO}$",
    ],
    "cancel": [
        r"^(?:please\s+)?(?:cancel|stop|abort) (?:the )?(?:shutdown|shut down|restart)$",
        rf"^(?:shutdown|restart) cancel {_DO}$",
        r"^(?:mat|don'?t) (?:shut down|band karo)$",
    ],
}
for _target, _patterns in PHRASES.items():
    intent("power", *_patterns, args=_p(_target), priority=9 if _target == "cancel" else 7)

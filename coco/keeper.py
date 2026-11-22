"""Restarts Coco, the dashboard, Ollama and Tailscale if any of them is missing."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from coco.core.paths import user_data_dir

log = logging.getLogger("coco.keeper")

HUB_PORT = 8765
OLLAMA_PORT = 11434
OFF_NOTE = "off_by_you"


def listening(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Something accepts connections on this port (even if it's busy answering)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def turned_off_by_you() -> bool:
    return (user_data_dir() / OFF_NOTE).exists()


def note_turned_off(off: bool) -> None:
    """Quit from her tray / the dashboard: off (keeper leaves her alone). Her start: on."""
    note = user_data_dir() / OFF_NOTE
    if off:
        note.write_text("Coco was turned off on purpose; the keeper won't restart her.\n")
    else:
        note.unlink(missing_ok=True)


@dataclass
class World:
    """What the keeper looks at and does (fakes in tests)."""

    coco_running: Callable[[], bool]
    coco_wanted: Callable[[], bool]  # Start with Windows on, and not Quit by you
    hub_wanted: Callable[[], bool]
    tailscale_wanted: Callable[[], bool]
    hub_up: Callable[[], bool]
    ollama_up: Callable[[], bool]
    tailscale_installed: Callable[[], bool]
    tailscale_running: Callable[[], bool]
    start_coco: Callable[[], None]
    start_hub: Callable[[], None]
    start_ollama: Callable[[], bool]
    start_tailscale: Callable[[], None]
    requested: Callable[[], set[str]] = set  # asked for by a start inside a package


def check(w: World) -> dict[str, str]:
    """One look around; starts whatever is missing. Returns what it found / did."""
    done: dict[str, str] = {}
    asked = w.requested()
    if w.tailscale_wanted() and w.tailscale_installed():
        if w.tailscale_running():
            done["tailscale"] = "running"
        else:
            w.start_tailscale()
            done["tailscale"] = "started"
    if w.hub_wanted() or "hub" in asked:
        if w.hub_up():
            done["dashboard"] = "running"
        else:
            w.start_hub()
            done["dashboard"] = "started"
    wanted = w.coco_wanted() or "coco" in asked
    running = w.coco_running()
    if wanted or running:
        if w.ollama_up():
            done["ollama"] = "running"
        else:
            done["ollama"] = "started" if w.start_ollama() else "not installed"
    if running:
        done["coco"] = "running"
    elif wanted:
        w.start_coco()  # (she brings up anything else herself)
        done["coco"] = "started"
    elif turned_off_by_you():
        done["coco"] = "off (you turned her off)"
    else:
        done["coco"] = "off (Start with Windows is off)"
    return done


def real_world() -> World:
    from coco.core import companions
    from coco.core.config import UserSettings
    from coco.core.launch import AUTOSTART_NAME, launch_command, launch_dir
    from coco.core.package_guard import take_requests
    from coco.desktop import windows as win
    from coco_hub.coco_link import CocoLink

    settings = UserSettings.load()

    def start_coco() -> None:
        env = {**os.environ, "COCO_FROM_KEEPER": "1"}  # (no "already running" box)
        subprocess.Popen(  # noqa: S603 - our own launcher
            launch_command(), cwd=launch_dir(), env=env, close_fds=True,
            creationflags=companions.CREATE_NO_WINDOW | companions.CREATE_NEW_PROCESS_GROUP,
        )  # fmt: skip

    def start_ollama() -> bool:
        from coco.brain.ollama_runtime import start_server

        return start_server()

    return World(
        coco_running=lambda: CocoLink().running,
        coco_wanted=lambda: win.autostart_enabled(AUTOSTART_NAME) and not turned_off_by_you(),
        hub_wanted=lambda: settings.start_dashboard,
        tailscale_wanted=lambda: settings.start_tailscale,
        hub_up=lambda: listening(HUB_PORT),
        ollama_up=lambda: listening(OLLAMA_PORT),
        tailscale_installed=companions.TAILSCALE_GUI.exists,
        tailscale_running=lambda: companions.running("tailscale-ipn.exe"),
        start_coco=start_coco,
        start_hub=companions.start_hub,
        start_ollama=start_ollama,
        start_tailscale=lambda: companions._spawn([str(companions.TAILSCALE_GUI)]),
        requested=take_requests,
    )


def main() -> int:
    from logging.handlers import RotatingFileHandler

    from coco.core import keeper_task
    from coco.core.package_guard import redirected

    if redirected() and not os.environ.get("COCO_ALLOW_PACKAGED"):
        return 0 if keeper_task.run_now() else 1  # (run it outside the package instead)

    from coco.core.paths import log_dir

    handler = RotatingFileHandler(log_dir() / "keeper.log", maxBytes=200_000, backupCount=1,
                                  encoding="utf-8")  # fmt: skip
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        import win32event

        mutex = win32event.CreateMutex(None, False, "Local\\CocoKeeper")  # noqa: F841 - held
        import win32api
        import winerror

        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            return 0  # another check is already running
        done = check(real_world())
    except Exception:  # noqa: BLE001 - log it; the next run tries again
        log.exception("keeper check failed")
        return 1
    started = [k for k, v in done.items() if v == "started"]
    if started or any(v != "running" for v in done.values()):
        log.info("%s  (%s)", "started " + ", ".join(started) if started else "checked",
                 ", ".join(f"{k} {v}" for k, v in done.items()))  # fmt: skip
    return 0

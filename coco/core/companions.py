"""Starts what Coco needs alongside her: Ollama, the dashboard and Tailscale."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from coco.core.launch import launch_command, launch_dir

log = logging.getLogger(__name__)

# CREATE_NO_WINDOW gives Ollama a hidden console that its helper processes share.
# With DETACHED_PROCESS each helper would open a console window of its own.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
HUB_URL = "http://127.0.0.1:8765/login"
TAILSCALE_GUI = (
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tailscale" / "tailscale-ipn.exe"
)


def ollama_exe() -> str | None:
    """ollama.exe: on PATH, or where the Windows installer puts it."""
    found = shutil.which("ollama")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(local) if local.exists() else None


def answers(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - localhost
            return r.status < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def running(exe_name: str) -> bool:
    try:
        import psutil

        name = exe_name.lower()
        return any(
            (p.info.get("name") or "").lower() == name for p in psutil.process_iter(["name"])
        )
    except Exception:  # noqa: BLE001 - can't tell: assume it runs (never start twice)
        return True


def _spawn(args: list[str], cwd: str | None = None) -> None:
    subprocess.Popen(  # noqa: S603 - fixed programs, no user input
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def _listening(port: int) -> bool:
    from coco.keeper import listening

    return listening(port)


def start_hub() -> None:
    _spawn(launch_command(hub=True), cwd=launch_dir())


def bring_up(
    *,
    hub: bool,
    tailscale: bool,
    ensure_ollama: Callable[[], bool],
    hub_up: Callable[[], bool] = lambda: _listening(8765),  # (a busy hub still counts)
    start_hub_: Callable[[], None] = start_hub,
    tailscale_installed: Callable[[], bool] = TAILSCALE_GUI.exists,
    tailscale_running: Callable[[], bool] = lambda: running("tailscale-ipn.exe"),
    start_tailscale: Callable[[], None] = lambda: _spawn([str(TAILSCALE_GUI)]),
) -> dict[str, str]:
    """Make sure the companions run. Returns what it found / did (for the log + tests)."""
    done: dict[str, str] = {}
    if tailscale and tailscale_installed():
        if tailscale_running():
            done["tailscale"] = "running"
        else:
            start_tailscale()
            done["tailscale"] = "started"
    if hub:
        if hub_up():
            done["dashboard"] = "running"
        else:
            start_hub_()
            done["dashboard"] = "started"
    try:
        done["ollama"] = "up" if ensure_ollama() else "down"
    except Exception as exc:  # noqa: BLE001 - never break Coco's start
        done["ollama"] = f"error: {exc}"
    log.info("Companions: %s", ", ".join(f"{k} {v}" for k, v in done.items()))
    return done

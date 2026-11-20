"""Starts the Ollama server if it isn't running."""

from __future__ import annotations

import logging
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable

log = logging.getLogger(__name__)

# CREATE_NO_WINDOW gives Ollama a hidden console that its helper processes share.
# With DETACHED_PROCESS each helper would open a console window of its own.
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def server_up(host: str, timeout: float = 1.0) -> bool:
    """True if the Ollama server answers on `host` (e.g. http://127.0.0.1:11434)."""
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/version", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def start_server() -> bool:
    """Launch `ollama serve` in the background. Returns False if Ollama isn't installed."""
    from coco.core.companions import ollama_exe

    exe = ollama_exe()  # (on PATH, or where the Windows installer puts it)
    if exe is None:
        log.warning("Ollama is not installed (ollama.exe not found)")
        return False
    log.info("Starting the Ollama server: %s serve", exe)
    subprocess.Popen(  # noqa: S603 - fixed command, no user input
        [exe, "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return True


def ensure_server(
    host: str,
    autostart: bool,
    wait: float = 15.0,
    *,
    is_up: Callable[[str], bool] = server_up,
    start: Callable[[], bool] = start_server,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True once the server is reachable (starting it first if allowed). Blocking: call
    it from a worker thread, never the Qt thread."""
    if is_up(host):
        return True
    if not autostart or not start():
        return False
    deadline = wait
    while deadline > 0:
        sleep(0.5)
        deadline -= 0.5
        if is_up(host):
            log.info("Ollama server is up")
            return True
    log.warning("Started Ollama but it didn't answer within %.0f s", wait)
    return False

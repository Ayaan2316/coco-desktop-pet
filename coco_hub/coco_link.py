"""The dashboard's client for Coco's local link."""

from __future__ import annotations

import contextlib
import ctypes
import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

from coco.core.launch import is_coco_process, launch_command, launch_dir
from coco.hub_link import link_file

log = logging.getLogger(__name__)

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259


def process_exe(pid: int) -> str | None:
    """Full path of a running process's program, or None if it isn't running."""
    if _k32 is None or pid <= 0:
        return None
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return None
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value
    finally:
        _k32.CloseHandle(h)


def _terminate(pid: int) -> None:
    h = _k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if h:
        _k32.TerminateProcess(h, 1)
        _k32.CloseHandle(h)


class CocoLink:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or link_file()

    # --- talking
    def _info(self) -> dict | None:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        exe = process_exe(int(info.get("pid", 0)))
        if not is_coco_process(exe):
            return None  # stale file: Coco isn't running
        return info

    def _call(self, method: str, path: str, body: dict | None = None, timeout: float = 6.0):
        info = self._info()
        if info is None:
            return None
        req = urllib.request.Request(
            f"http://127.0.0.1:{info['port']}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {info['token']}",
                     "Content-Type": "application/json"},
        )  # fmt: skip
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - localhost only
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}").get("error", str(exc))
            except ValueError:
                detail = str(exc)
            raise RuntimeError(detail) from exc
        except (OSError, ValueError):
            return None

    @property
    def running(self) -> bool:
        return self._info() is not None

    def status(self) -> dict | None:
        return self._call("GET", "/status", timeout=3.0)

    def command(self, name: str, **kw) -> dict | None:
        return self._call("POST", "/command", {"name": name, **kw})

    def chat(self, text: str) -> int | None:
        r = self._call("POST", "/chat", {"text": text})
        return r.get("chat_id") if r else None

    # --- on / off
    def start(self, wait: float = 25.0) -> bool:
        """Start Coco (no console window). True once her link answers."""
        if self.running:
            return True
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(  # noqa: S603 - our own launcher
            launch_command(),
            cwd=launch_dir(),
            creationflags=flags,
            close_fds=True,
        )
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if self.status() is not None:
                return True
        return self.running

    def stop(self, wait: float = 10.0) -> bool:
        """Ask Coco to quit (like the tray's Quit). True once she's gone."""
        info = self._info()
        if info is None:
            return True
        pid = int(info["pid"])
        with contextlib.suppress(RuntimeError):
            self.command("quit")
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if process_exe(pid) is None:
                return True
            time.sleep(0.3)
        exe = process_exe(pid)
        if is_coco_process(exe):
            log.warning("Coco didn't quit in %.0f s: ending process %d", wait, pid)
            _terminate(pid)
            time.sleep(0.5)
        return process_exe(pid) is None

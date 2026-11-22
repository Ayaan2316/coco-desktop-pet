"""Detects when Coco was started inside a packaged app, where Windows redirects AppData."""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def redirected_to(base: Path | None = None) -> Path | None:
    """The package folder our %LOCALAPPDATA%\\Coco writes really go to, or None (normal)."""
    local = base or Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    name = f".where_am_i_{uuid.uuid4().hex[:10]}"
    probe = local / "Coco" / name
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("x", encoding="utf-8")
        try:
            hits = list((local / "Packages").glob(f"*/LocalCache/Local/Coco/{name}"))
        finally:
            probe.unlink(missing_ok=True)
    except OSError:
        return None
    return hits[0].parents[3] if hits else None


def packaged_ancestor() -> str | None:
    """Package name of the packaged app we were started from, or None.

    Our own process doesn't report a package, but an ancestor does, and Windows may
    redirect some of our AppData writes because of it.
    """
    import ctypes
    from ctypes import wintypes

    import psutil

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    try:
        parents = psutil.Process().parents()
    except psutil.Error:
        return None
    for p in parents:
        h = k32.OpenProcess(0x1000, False, p.pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            n = ctypes.c_uint32(0)
            if k32.GetPackageFullName(h, ctypes.byref(n), None) == 15700:  # no package
                continue
            buf = ctypes.create_unicode_buffer(max(n.value, 1))
            if k32.GetPackageFullName(h, ctypes.byref(n), buf) == 0 and buf.value:
                return buf.value
        finally:
            k32.CloseHandle(h)
    return None


def redirected() -> bool:
    """Started from inside a packaged app (or our AppData writes land in a package's copy)."""
    return packaged_ancestor() is not None or redirected_to() is not None


def request_file() -> Path:
    """Outside AppData (never redirected): how a packaged start asks the keeper for Coco."""
    return Path(os.environ.get("USERPROFILE") or Path.home()) / ".coco" / "start_request"


def hand_off(what: str) -> bool:
    """We're inside a package: ask the keeper (Task Scheduler) to start `what` ("coco" or
    "hub") the normal way. True if it was handed off (the caller should exit)."""
    from coco.core import keeper_task

    try:
        if not keeper_task.install():
            return False
        req = request_file()
        req.parent.mkdir(parents=True, exist_ok=True)
        wanted = set(req.read_text(encoding="utf-8").split()) if req.exists() else set()
        req.write_text(" ".join(sorted(wanted | {what})), encoding="utf-8")
        return keeper_task.run_now()
    except OSError:
        return False


def take_requests() -> set[str]:
    """Keeper: what a packaged start asked for (and forget it)."""
    req = request_file()
    try:
        wanted = set(req.read_text(encoding="utf-8").split())
        req.unlink(missing_ok=True)
        return wanted
    except OSError:
        return set()

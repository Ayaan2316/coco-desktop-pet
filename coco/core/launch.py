"""Command lines for starting Coco, the dashboard and the keeper, from source or as Coco.exe."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from coco.core.paths import FROZEN, PROJECT_ROOT

AUTOSTART_NAME = "Coco desktop cat"  # the HKCU\...\Run value


def launch_command(
    hub: bool = False, frozen: bool = FROZEN, exe: str | None = None, keeper: bool = False
) -> list[str]:
    exe = exe or sys.executable
    if frozen:
        return [exe, "--keep"] if keeper else [exe, "--hub"] if hub else [exe]
    pythonw = str(Path(exe).with_name("pythonw.exe"))
    script = "run_keeper.pyw" if keeper else "run_hub.pyw" if hub else "run_coco.pyw"
    return [pythonw, str(PROJECT_ROOT / script)]


def launch_dir(frozen: bool = FROZEN, exe: str | None = None) -> str:
    """The working folder to start her in."""
    return str(Path(exe or sys.executable).parent) if frozen else str(PROJECT_ROOT)


def command_line(args: list[str]) -> str:
    """One line for the Run key: every part quoted, flags as they are."""
    return " ".join(a if a.startswith("--") else f'"{a}"' for a in args)


def autostart_command(frozen: bool = FROZEN, exe: str | None = None) -> str:
    return command_line(launch_command(frozen=frozen, exe=exe))


def is_coco_process(exe_path: str | None) -> bool:
    """Is this program Coco? (Python running her from source, or Coco.exe.)"""
    if not exe_path:
        return False
    name = os.path.basename(exe_path).lower()
    return name.startswith("python") or name == "coco.exe"


def other_coco_pids(processes=None, me: int | None = None) -> list[int]:
    """PIDs of other running Coco processes (not the dashboard, keeper or a selftest).

    Backs up the single-instance mutex, which doesn't see a Coco started from inside
    a packaged app.
    """
    import psutil

    me = os.getpid() if me is None else me
    family = {me}
    try:
        family.update(p.pid for p in psutil.Process(me).parents())  # (the venv launcher)
        family.update(p.pid for p in psutil.Process(me).children(recursive=True))
    except psutil.Error:
        pass
    found = []
    for p in processes if processes is not None else psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = [str(a) for a in (p.info.get("cmdline") or [])]
        except (psutil.Error, AttributeError):
            continue
        if not cmd or p.info["pid"] in family:
            continue
        name = os.path.basename(cmd[0]).lower()
        if any(a.lower().endswith("run_coco.pyw") for a in cmd[1:]) or (
            name == "coco.exe" and not {"--hub", "--keep", "--selftest"} & set(cmd[1:])
        ):
            found.append(p.info["pid"])
    return found


def just_signed_in(within: float = 180.0) -> bool:
    """Is this the first few minutes after you signed in? (Then "Start with Windows" and the
    keeper may both start her; the second one leaves quietly instead of showing a box.)"""
    import time

    import psutil

    try:
        started = [p.info["create_time"] for p in psutil.process_iter(["name", "create_time"])
                   if (p.info.get("name") or "").lower() == "explorer.exe"]  # fmt: skip
        return bool(started) and time.time() - min(started) < within
    except psutil.Error:
        return False


def migrate_autostart(
    read: Callable[[str], str | None],
    write: Callable[[str, str], None],
    frozen: bool = FROZEN,
    exe: str | None = None,
) -> bool:
    """Point an old source-version "Start with Windows" entry at this Coco.exe.

    Only for an installed exe (under ...\\Programs\\Coco), only our own entry, and it
    never turns autostart on.
    """
    if not frozen or "programs" not in Path(exe or sys.executable).parent.parent.name.lower():
        return False
    current = read(AUTOSTART_NAME)
    if not current:
        return False
    wanted = autostart_command(frozen=True, exe=exe)
    if current == wanted or "run_coco.pyw" not in current.lower():
        return False
    write(AUTOSTART_NAME, wanted)
    return True

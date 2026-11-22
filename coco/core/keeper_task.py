"""Installs and runs the "Coco keeper" task in Task Scheduler."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from coco.core.launch import launch_command, launch_dir

log = logging.getLogger(__name__)

TASK = "Coco keeper"
CREATE_NO_WINDOW = 0x08000000

# Waking up: Modern Standby (Kernel-Power 507) or classic sleep / hibernate (Power-Troubleshooter 1)
WAKE_QUERY = (
    '<QueryList><Query Id="0" Path="System"><Select Path="System">'
    "*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=507]] or "
    "*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]"
    "</Select></Query></QueryList>"
)


def user_id() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    name = os.environ.get("USERNAME", "")
    return f"{domain}\\{name}" if domain else name


def task_xml(command: list[str], workdir: str, user: str, now: datetime | None = None) -> str:
    start = (now or datetime.now()).replace(microsecond=0).isoformat()
    exe, args = command[0], " ".join(a if a.startswith("--") else f'"{a}"' for a in command[1:])
    u = escape(user)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Brings Coco, her dashboard, Ollama and Tailscale back if they are gone
      (after sleep, an unfinished shutdown or a freeze). Made by Coco; turn it off in her
      dashboard: Settings.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{u}</UserId><Delay>PT40S</Delay></LogonTrigger>
    <SessionStateChangeTrigger><Enabled>true</Enabled><StateChange>SessionUnlock</StateChange><UserId>{u}</UserId><Delay>PT3S</Delay></SessionStateChangeTrigger>
    <EventTrigger><Enabled>true</Enabled><Subscription>{escape(WAKE_QUERY)}</Subscription><Delay>PT5S</Delay></EventTrigger>
    <TimeTrigger><Enabled>true</Enabled><StartBoundary>{start}</StartBoundary><Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{u}</UserId><LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(exe)}</Command><Arguments>{escape(args)}</Arguments><WorkingDirectory>{escape(workdir)}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - Windows' own tool, fixed arguments
        ["schtasks", *args], capture_output=True, text=True, check=False,
        creationflags=CREATE_NO_WINDOW,
    )  # fmt: skip


def installed_command() -> str | None:
    """The command line the task runs now, or None if there's no task."""
    r = _schtasks("/Query", "/TN", TASK, "/XML")
    if r.returncode != 0:
        return None
    import re

    cmd = re.search(r"<Command>(.*?)</Command>", r.stdout, re.S)
    args = re.search(r"<Arguments>(.*?)</Arguments>", r.stdout, re.S)
    return f"{cmd.group(1) if cmd else ''} {args.group(1) if args else ''}".strip()


def wanted_command() -> str:
    command = launch_command(keeper=True)
    xml = task_xml(command, launch_dir(), user_id())
    import re

    cmd = re.search(r"<Command>(.*?)</Command>", xml).group(1)
    args = re.search(r"<Arguments>(.*?)</Arguments>", xml).group(1)
    return f"{cmd} {args}".strip()


def install() -> bool:
    """Create (or update) the task. True if it's in place afterwards."""
    if installed_command() == wanted_command():
        return True
    xml = task_xml(launch_command(keeper=True), launch_dir(), user_id())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "coco_keeper.xml"
        path.write_text(xml, encoding="utf-16")
        r = _schtasks("/Create", "/TN", TASK, "/XML", str(path), "/F")
    if r.returncode != 0:
        log.warning("Couldn't create the keeper task: %s", (r.stderr or r.stdout).strip())
        return False
    log.info("Keeper task installed: %s", wanted_command())
    return True


def uninstall() -> bool:
    if installed_command() is None:
        return True
    r = _schtasks("/Delete", "/TN", TASK, "/F")
    return r.returncode == 0


def run_now() -> bool:
    """Run the keeper through Task Scheduler (so what it starts doesn't belong to us)."""
    return _schtasks("/Run", "/TN", TASK).returncode == 0

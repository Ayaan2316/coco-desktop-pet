"""Battery, CPU, RAM and GPU status."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from coco.tools.intents import intent
from coco.tools.registry import ToolCall, ToolResult, tool

log = logging.getLogger(__name__)


@dataclass
class Status:
    battery: float | None = None  # percent
    plugged: bool | None = None
    minutes_left: float | None = None
    cpu: float | None = None  # percent
    ram: float | None = None  # percent
    ram_used_gb: float | None = None
    ram_total_gb: float | None = None
    gpu_name: str = ""
    gpu_temp: float | None = None  # °C
    gpu_use: float | None = None  # percent
    gpu_mem_used_gb: float | None = None
    gpu_mem_total_gb: float | None = None


def battery_only() -> tuple[float, bool] | None:
    """(percent, plugged) or None (desktop PC / no battery). Cheap: for the 60 s watch."""
    try:
        import psutil

        b = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        return None
    return None if b is None else (float(b.percent), bool(b.power_plugged))


def read_status(cpu_interval: float = 0.3) -> Status:
    s = Status()
    try:
        import psutil

        b = psutil.sensors_battery()
        if b is not None:
            s.battery, s.plugged = float(b.percent), bool(b.power_plugged)
            if b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
                s.minutes_left = b.secsleft / 60
        s.cpu = psutil.cpu_percent(interval=cpu_interval)
        vm = psutil.virtual_memory()
        s.ram, s.ram_used_gb, s.ram_total_gb = vm.percent, vm.used / 2**30, vm.total / 2**30
    except Exception:
        log.debug("psutil failed", exc_info=True)
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(h)
            s.gpu_name = name.decode() if isinstance(name, bytes) else str(name)
            s.gpu_temp = float(pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU))
            s.gpu_use = float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            s.gpu_mem_used_gb, s.gpu_mem_total_gb = mem.used / 2**30, mem.total / 2**30
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        log.debug("no NVIDIA GPU info", exc_info=True)
    return s


def describe(s: Status, what: str = "all") -> str:
    """Plain facts (for her to put in her own words, or to say as they are)."""
    parts = []
    if what in ("all", "battery") and s.battery is not None:
        charge = "charging" if s.plugged else "NOT plugged in"
        left = f", about {int(s.minutes_left)} minutes left" if s.minutes_left else ""
        parts.append(f"Battery {s.battery:.0f}% ({charge}{left})")
    if what in ("all", "cpu") and s.cpu is not None:
        parts.append(f"CPU {s.cpu:.0f}% busy")
    if what in ("all", "ram") and s.ram is not None:
        parts.append(f"RAM {s.ram:.0f}% used ({s.ram_used_gb:.1f} of {s.ram_total_gb:.0f} GB)")
    if what in ("all", "gpu") and s.gpu_temp is not None:
        parts.append(
            f"GPU {s.gpu_temp:.0f}°C, {s.gpu_use:.0f}% busy, "
            f"{s.gpu_mem_used_gb:.1f} of {s.gpu_mem_total_gb:.0f} GB memory used"
        )
    if not parts:
        return "I couldn't read that on this PC."
    return "; ".join(parts) + "."


@tool(
    "system_status",
    "check the PC: battery, CPU, RAM, GPU temperature (target: battery | cpu | ram | gpu | all)",
    words=r"battery|charge|charging|cpu|ram|memory|gpu|temperature|temp|hot|garam|busy|slow|"
    r"system|status|pc|laptop|computer|performance",
    label="System status",
)
def system_status(call: ToolCall, ctx) -> ToolResult:
    what = call.arg("target", "all").lower()
    what = what if what in ("battery", "cpu", "ram", "gpu") else "all"
    reader = ctx.services.get("read_status", read_status)
    facts = describe(reader(), what)
    # facts -> the AI says it in her own words (the app); `say` if the AI is off
    return ToolResult(say=facts, facts=f"You checked their PC: {facts}", emotion="curious")


# --- phrases
def _what(m: re.Match, said: str) -> dict[str, str]:
    for key, pattern in (("battery", r"battery|charge|charging|charj"), ("gpu", r"gpu|graphics"),
                         ("cpu", r"cpu|processor"), ("ram", r"\bram\b|memory")):  # fmt: skip
        if re.search(pattern, said):
            return {"target": key}
    return {"target": "all"}


intent(
    "system_status",
    r"\b(?:how'?s|how is|what'?s|what is|check|show)\b.{0,20}\b(?:battery|charge|cpu|ram|"
    r"memory|gpu|system|pc|laptop)\b",
    r"\b(?:how much|how)\b.{0,12}\b(?:battery|charge)\b",
    r"\bhow (?:hot|warm) is\b.{0,15}\b(?:gpu|cpu|laptop|pc)\b",
    r"\bis (?:my )?(?:pc|laptop|computer|system) (?:busy|slow|hot|ok)\b",
    r"^(?:system status|battery|battery status|pc status)\??$",
    r"\b(?:battery|charge|charging|cpu|ram|gpu)\b.{0,12}\b(?:kitni|kitna|kya|kaisa|kaisi)\b",
    r"\b(?:gpu|cpu|laptop|pc)\b.{0,12}\b(?:garam|hot)\b",
    args=_what,
    questions=True,
    priority=6,
)

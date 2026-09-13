"""The Host's live headroom: CPU, memory, and the disk this checkout lives on.

The window calls `sample()`. Tests replace it. A failure raises SamplerError;
the window degrades the widget and still draws the board.

CPU is a /proc/stat delta against the previous sample so a later request does
not sleep; the first call takes a short second reading so the figure is still
CPU, not load.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

# The checkout the window serves. Sampling `/` would report a disk this
# checkout does not live on, on a machine with more than one filesystem.
DISK_ROOT = Path(__file__).resolve().parents[1]


class SamplerError(Exception):
    """The host could not be sampled."""


@dataclass(frozen=True)
class Sample:
    cpu_percent: float
    memory_used: int
    memory_total: int
    disk_used: int
    disk_total: int
    disk_path: str


_prev_cpu: tuple[int, int] | None = None


def sample() -> Sample:
    """CPU, memory, and disk, as this machine reports them right now."""
    try:
        cpu = _cpu_percent()
        memory_used, memory_total = _memory()
        disk = shutil.disk_usage(DISK_ROOT)
    except (OSError, ValueError, KeyError) as exc:
        raise SamplerError(str(exc)) from exc
    return Sample(
        cpu_percent=cpu,
        memory_used=memory_used,
        memory_total=memory_total,
        disk_used=disk.used,
        disk_total=disk.total,
        disk_path=str(DISK_ROOT),
    )


def _cpu_times() -> tuple[int, int]:
    """idle ticks, total ticks from the aggregate `cpu` line of /proc/stat."""
    with open("/proc/stat", encoding="ascii") as proc:
        parts = proc.readline().split()
    nums = [int(x) for x in parts[1:]]
    # idle + iowait; guest columns sit past 8 and would double-count.
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    total = sum(nums[:8])
    return idle, total


def _cpu_ratio(idle0: int, total0: int, idle1: int, total1: int) -> float:
    didle = idle1 - idle0
    dtotal = total1 - total0
    if dtotal <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - didle / dtotal)))


def _cpu_percent() -> float:
    global _prev_cpu
    idle, total = _cpu_times()
    prev = _prev_cpu
    if prev is None:
        time.sleep(0.05)
        later = _cpu_times()
        _prev_cpu = later
        return _cpu_ratio(idle, total, later[0], later[1])
    _prev_cpu = (idle, total)
    return _cpu_ratio(prev[0], prev[1], idle, total)


def _memory() -> tuple[int, int]:
    """used bytes, total bytes from /proc/meminfo.

    Used is Total minus Available, so cache the kernel can reclaim is not
    counted as pressure. MemAvailable missing is a sampler failure, not a
    reconstructed guess from MemFree.
    """
    values: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as proc:
        for line in proc:
            key, _, rest = line.partition(":")
            values[key] = int(rest.strip().split()[0]) * 1024
    total = values["MemTotal"]
    used = max(0, total - values["MemAvailable"])
    return used, total

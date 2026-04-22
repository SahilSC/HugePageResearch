"""Shared replay-time system tuning helpers.

These helpers centralize the host THP and background-memory-management knobs
used by both Redis replay and the deterministic GUPS split harness.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_THP_PATH = Path("/sys/kernel/mm/transparent_hugepage/enabled")
_THP_DEFRAG_PATH = Path("/sys/kernel/mm/transparent_hugepage/defrag")
_KHUGEPAGED_SLEEP_PATH = Path(
    "/sys/kernel/mm/transparent_hugepage/khugepaged/scan_sleep_millisecs"
)
_NUMA_BALANCING_PATH = Path("/proc/sys/kernel/numa_balancing")
_SWAPPINESS_PATH = Path("/proc/sys/vm/swappiness")
_OVERCOMMIT_PATH = Path("/proc/sys/vm/overcommit_memory")
_COMPACTION_PROACTIVENESS_PATH = Path("/proc/sys/vm/compaction_proactiveness")
_KSM_PATH = Path("/sys/kernel/mm/ksm/run")


@dataclass(frozen=True)
class SystemConfig:
    """Saved state of kernel tunables modified during benchmarking.

    Optional fields are ``None`` when the corresponding sysfs/procfs path does
    not exist on the host kernel.
    """

    thp_enabled: str
    thp_defrag: str
    khugepaged_scan_sleep_ms: str
    numa_balancing: str
    swappiness: str
    overcommit_memory: str
    compaction_proactiveness: str | None
    ksm_run: str | None


def _sysfs_write(path: Path, value: str) -> None:
    """Write *value* to a sysfs/procfs file via a privileged bash redirect."""
    subprocess.check_call(
        ["sudo", "bash", "-c", f"echo {value} > {path}"],
        stdout=subprocess.DEVNULL,
    )


def _set_thp_enabled_mode(mode: str) -> None:
    """Set the host THP enabled mode for the next benchmark restore.

    Args:
        mode: THP enabled mode to apply before the next restore.

    Raises:
        RuntimeError: If *mode* is not supported by the replay harness.
    """
    if mode not in {"always", "never"}:
        raise RuntimeError(
            f"Unsupported replay THP mode {mode!r}; expected 'always' or 'never'."
        )
    _sysfs_write(_THP_PATH, mode)


def setup_system() -> SystemConfig:
    """Snapshot kernel tunables and apply the replay harness defaults."""
    config = SystemConfig(
        thp_enabled=_THP_PATH.read_text().strip(),
        thp_defrag=_THP_DEFRAG_PATH.read_text().strip(),
        khugepaged_scan_sleep_ms=_KHUGEPAGED_SLEEP_PATH.read_text().strip(),
        numa_balancing=_NUMA_BALANCING_PATH.read_text().strip(),
        swappiness=_SWAPPINESS_PATH.read_text().strip(),
        overcommit_memory=_OVERCOMMIT_PATH.read_text().strip(),
        compaction_proactiveness=(
            _COMPACTION_PROACTIVENESS_PATH.read_text().strip()
            if _COMPACTION_PROACTIVENESS_PATH.exists()
            else None
        ),
        ksm_run=(_KSM_PATH.read_text().strip() if _KSM_PATH.exists() else None),
    )

    _sysfs_write(_THP_PATH, "always")
    _sysfs_write(_THP_DEFRAG_PATH, "never")
    _sysfs_write(_KHUGEPAGED_SLEEP_PATH, "4294967295")
    _sysfs_write(_NUMA_BALANCING_PATH, "0")
    _sysfs_write(_SWAPPINESS_PATH, "0")
    _sysfs_write(_OVERCOMMIT_PATH, "1")
    if config.compaction_proactiveness is not None:
        _sysfs_write(_COMPACTION_PROACTIVENESS_PATH, "0")
    if config.ksm_run is not None:
        _sysfs_write(_KSM_PATH, "0")
    return config


def teardown_system(config: SystemConfig) -> None:
    """Restore kernel tunables to the values saved by :func:`setup_system`."""
    for path, raw in [
        (_THP_PATH, config.thp_enabled),
        (_THP_DEFRAG_PATH, config.thp_defrag),
    ]:
        match = re.search(r"\[(\w+)\]", raw)
        _sysfs_write(path, match.group(1) if match else raw)

    _sysfs_write(_KHUGEPAGED_SLEEP_PATH, config.khugepaged_scan_sleep_ms)
    _sysfs_write(_NUMA_BALANCING_PATH, config.numa_balancing)
    _sysfs_write(_SWAPPINESS_PATH, config.swappiness)
    _sysfs_write(_OVERCOMMIT_PATH, config.overcommit_memory)
    if config.compaction_proactiveness is not None:
        _sysfs_write(_COMPACTION_PROACTIVENESS_PATH, config.compaction_proactiveness)
    if config.ksm_run is not None:
        _sysfs_write(_KSM_PATH, config.ksm_run)


"""Replay-local hardware counters for Redis replay experiments.

This module uses direct ``perf_event_open`` counters for the Redis thread group
being replayed. Replay only needs begin/end totals, so this collector opens the
target thread set once, enables the counters for the timed window, then reads
the final cumulative counts without any perf-buffer polling.
"""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from dataclasses import dataclass
from fcntl import ioctl
from pathlib import Path
from typing import Final

from data_schema.perf.perf_schema import PerfHWCacheConfig

_PERF_EVENT_OPEN_NR: Final[int] = 298
_PERF_TYPE_HW_CACHE: Final[int] = 3
_PERF_EVENT_IOC_ENABLE: Final[int] = ord("$") << (4 * 2) | 0
_PERF_EVENT_IOC_DISABLE: Final[int] = ord("$") << (4 * 2) | 1
_PERF_ATTR_FLAG_DISABLED: Final[int] = 1 << 0
_COUNTER_READ_BYTES: Final[int] = 8

_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.syscall.restype = ctypes.c_long


@dataclass(frozen=True)
class DTLBCounterMetrics:
    """dTLB counters captured during one replay run.

    Attributes:
        dtlb_loads: Total dTLB read accesses for the target Redis thread set.
        dtlb_misses: Total dTLB read misses for the target Redis thread set.
    """

    dtlb_loads: int
    dtlb_misses: int


@dataclass(frozen=True)
class _CounterSpec:
    """Definition for one direct replay perf counter.

    Attributes:
        name: Stable result name used in replay output and error messages.
        config: ``perf_event_open`` cache-event configuration integer.
    """

    name: str
    config: int


class _PerfEventAttr(ctypes.Structure):
    """Subset of ``struct perf_event_attr`` needed for direct counting."""

    _fields_ = [
        ("type", ctypes.c_uint),
        ("size", ctypes.c_uint),
        ("config", ctypes.c_ulonglong),
        ("sample_period", ctypes.c_ulonglong),
        ("sample_type", ctypes.c_ulonglong),
        ("read_format", ctypes.c_ulonglong),
        ("flags", ctypes.c_ulonglong),
        ("wakeup_events", ctypes.c_uint),
        ("bp_type", ctypes.c_uint),
        ("config1", ctypes.c_ulonglong),
        ("config2", ctypes.c_ulonglong),
        ("branch_sample_type", ctypes.c_ulonglong),
        ("sample_regs_user", ctypes.c_ulonglong),
        ("sample_stack_user", ctypes.c_uint),
        ("clockid", ctypes.c_int),
        ("sample_regs_intr", ctypes.c_ulonglong),
        ("aux_watermark", ctypes.c_uint),
        ("sample_max_stack", ctypes.c_ushort),
        ("__reserved_2", ctypes.c_ushort),
        ("aux_sample_size", ctypes.c_uint),
        ("__reserved_3", ctypes.c_uint),
        ("sig_data", ctypes.c_ulonglong),
        ("config3", ctypes.c_ulonglong),
    ]


_DTLB_LOADS_SPEC = _CounterSpec(
    name="dtlb_loads",
    config=PerfHWCacheConfig.config(
        cache=PerfHWCacheConfig.Cache.PERF_COUNT_HW_CACHE_DTLB,
        op=PerfHWCacheConfig.Op.PERF_COUNT_HW_CACHE_OP_READ,
        result=PerfHWCacheConfig.Result.PERF_COUNT_HW_CACHE_RESULT_ACCESS,
    ),
)
_DTLB_MISSES_SPEC = _CounterSpec(
    name="dtlb_misses",
    config=PerfHWCacheConfig.config(
        cache=PerfHWCacheConfig.Cache.PERF_COUNT_HW_CACHE_DTLB,
        op=PerfHWCacheConfig.Op.PERF_COUNT_HW_CACHE_OP_READ,
        result=PerfHWCacheConfig.Result.PERF_COUNT_HW_CACHE_RESULT_MISS,
    ),
)
_COUNTER_SPECS: Final[dict[str, _CounterSpec]] = {
    _DTLB_LOADS_SPEC.name: _DTLB_LOADS_SPEC,
    _DTLB_MISSES_SPEC.name: _DTLB_MISSES_SPEC,
}


class _PerfCounterSession:
    """Active direct-perf collection for one counter across one Redis TGID.

    Example thread set:
        [608669, 608670, 608671]

    Args:
        spec: Counter definition being collected.
        target_tgid: Redis thread-group ID being measured.
        thread_ids: Stable thread set opened at replay start.
        fds: Open perf file descriptors, one per thread in ``thread_ids``.
    """

    def __init__(
        self,
        spec: _CounterSpec,
        target_tgid: int,
        thread_ids: list[int],
        fds: list[int],
    ):
        self._spec = spec
        self._target_tgid = target_tgid
        self._thread_ids = tuple(thread_ids)
        self._fds = list(fds)
        self._closed = False

    @classmethod
    def start(
        cls,
        spec: _CounterSpec,
        target_tgid: int,
        thread_ids: list[int],
    ) -> "_PerfCounterSession":
        """Open and enable one counter for every thread in ``thread_ids``.

        Args:
            spec: Counter definition to open.
            target_tgid: Redis thread-group ID being measured.
            thread_ids: Thread IDs discovered from ``/proc/<tgid>/task``.

        Returns:
            An active counter session ready for later ``stop()``.

        Raises:
            PermissionError: If the host disallows opening the counter.
            RuntimeError: If the counter is unsupported or setup fails.
        """
        fds: list[int] = []
        try:
            for thread_id in thread_ids:
                attr = _build_counter_attr(spec.config)
                try:
                    fd = _perf_event_open(attr=attr, thread_id=thread_id)
                except OSError as exc:
                    _raise_counter_open_error(
                        spec=spec,
                        target_tgid=target_tgid,
                        thread_id=thread_id,
                        error=exc,
                    )
                fds.append(fd)

            for fd in fds:
                ioctl(fd, _PERF_EVENT_IOC_ENABLE, 0)
        except Exception:
            _disable_and_close_fds(fds)
            raise

        return cls(
            spec=spec,
            target_tgid=target_tgid,
            thread_ids=thread_ids,
            fds=fds,
        )

    def stop(self) -> int:
        """Disable this counter and sum the final per-thread totals.

        Returns:
            Final cumulative count across the thread set opened at start.
        """
        if self._closed:
            raise RuntimeError(
                f"{self._spec.name} counters for Redis pid {self._target_tgid} "
                "have already been stopped."
            )

        try:
            for fd in self._fds:
                ioctl(fd, _PERF_EVENT_IOC_DISABLE, 0)

            total = 0
            for fd in self._fds:
                total += _read_counter_value(fd, self._spec, self._target_tgid)
            return total
        finally:
            _close_fds(self._fds)
            self._fds.clear()
            self._closed = True

    def close(self) -> None:
        """Best-effort cleanup for an active counter session."""
        if self._closed:
            return

        _disable_and_close_fds(self._fds)
        self._fds.clear()
        self._closed = True


def _build_counter_attr(config: int) -> _PerfEventAttr:
    """Build the direct-counting perf attr used for replay counters."""
    return _PerfEventAttr(
        type=_PERF_TYPE_HW_CACHE,
        size=ctypes.sizeof(_PerfEventAttr),
        config=config,
        sample_period=0,
        sample_type=0,
        read_format=0,
        flags=_PERF_ATTR_FLAG_DISABLED,
    )


def _perf_event_open(attr: _PerfEventAttr, thread_id: int) -> int:
    """Open one perf counter for ``thread_id`` on any CPU."""
    ctypes.set_errno(0)
    fd = _LIBC.syscall(
        ctypes.c_long(_PERF_EVENT_OPEN_NR),
        ctypes.byref(attr),
        ctypes.c_int(thread_id),
        ctypes.c_int(-1),
        ctypes.c_int(-1),
        ctypes.c_ulong(0),
    )
    if fd >= 0:
        return int(fd)

    err = ctypes.get_errno() or errno.EIO
    raise OSError(err, os.strerror(err))


def _list_task_thread_ids(target_tgid: int) -> list[int]:
    """Return the current thread IDs for one Redis thread group.

    Example output:
        [608669, 608670, 608671]

    Args:
        target_tgid: Redis thread-group ID whose task directory will be read.

    Returns:
        Sorted thread IDs from ``/proc/<tgid>/task``.

    Raises:
        RuntimeError: If the target process has no readable task directory.
    """
    if target_tgid <= 0:
        raise RuntimeError(f"Redis pid must be positive, got {target_tgid!r}")

    task_dir = Path(f"/proc/{target_tgid}/task")
    if not task_dir.is_dir():
        raise RuntimeError(
            f"Redis pid {target_tgid} does not have a readable /proc task directory."
        )

    thread_ids = sorted(int(entry.name) for entry in task_dir.iterdir())
    if not thread_ids:
        raise RuntimeError(f"Redis pid {target_tgid} did not expose any task threads.")
    return thread_ids


def _read_counter_value(fd: int, spec: _CounterSpec, target_tgid: int) -> int:
    """Read one disabled replay counter as a native ``u64``."""
    raw_count = os.read(fd, _COUNTER_READ_BYTES)
    if len(raw_count) != _COUNTER_READ_BYTES:
        raise RuntimeError(
            f"Expected {_COUNTER_READ_BYTES} bytes while reading {spec.name} for "
            f"Redis pid {target_tgid}, got {len(raw_count)}."
        )
    return int.from_bytes(raw_count, byteorder=sys.byteorder, signed=False)


def _read_perf_event_paranoid() -> str | None:
    """Return the host ``perf_event_paranoid`` value when available."""
    path = Path("/proc/sys/kernel/perf_event_paranoid")
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def _raise_counter_open_error(
    spec: _CounterSpec,
    target_tgid: int,
    thread_id: int,
    error: OSError,
) -> None:
    """Raise a fail-fast replay error for one ``perf_event_open`` failure."""
    if error.errno in {errno.EACCES, errno.EPERM}:
        paranoid_value = _read_perf_event_paranoid()
        paranoid_hint = (
            f" Current /proc/sys/kernel/perf_event_paranoid={paranoid_value}."
            if paranoid_value is not None
            else ""
        )
        raise PermissionError(
            f"Could not open {spec.name} replay counter for Redis pid {target_tgid} "
            f"thread {thread_id}: {error.strerror}. Run replay under sudo/root and "
            f"check perf-event permissions.{paranoid_hint}"
        ) from error

    if error.errno in {
        errno.EINVAL,
        errno.ENOENT,
        errno.ENODEV,
        errno.EOPNOTSUPP,
        errno.ENOSYS,
    }:
        raise RuntimeError(
            f"This host does not support the {spec.name} replay counter needed for "
            f"the replay collector config: {error.strerror}."
        ) from error

    raise RuntimeError(
        f"Could not open {spec.name} replay counter for Redis pid {target_tgid} "
        f"thread {thread_id}: errno={error.errno} ({error.strerror})."
    ) from error


def _close_fds(fds: list[int]) -> None:
    """Best-effort close of every perf file descriptor in ``fds``."""
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            continue


def _disable_and_close_fds(fds: list[int]) -> None:
    """Best-effort disable and close of every perf file descriptor in ``fds``."""
    for fd in fds:
        try:
            ioctl(fd, _PERF_EVENT_IOC_DISABLE, 0)
        except OSError:
            continue
    _close_fds(fds)


class HardwareCollector:
    """Manages perf hardware counter sessions for one replay run.

    Each instance tracks its own active counter sessions, so multiple
    concurrent replays do not share mutable state.

    Example usage::

        collector = HardwareCollector()
        collector.start_counters(["dtlb_loads", "dtlb_misses"], redis_pid)
        # ... run replay ...
        totals = collector.stop_counters(["dtlb_loads", "dtlb_misses"])
        print(totals)  # {"dtlb_loads": 300, "dtlb_misses": 7}
    """

    def __init__(self) -> None:
        self._active_counters: dict[str, _PerfCounterSession] = {}

    @staticmethod
    def supported_counter_names() -> tuple[str, ...]:
        """Return replay counter names accepted by the collector config.

        Example output:
            ("dtlb_loads", "dtlb_misses")

        Returns:
            Stable replay counter names supported by this module.
        """
        return tuple(_COUNTER_SPECS)

    def start_counters(self, counter_names: list[str], target_tgid: int) -> None:
        """Start the configured replay counters for one Redis thread group.

        Example counter_names:
            ["dtlb_loads", "dtlb_misses"]

        Args:
            counter_names: Replay counters requested for the timed window.
            target_tgid: Redis thread-group ID being measured.

        """
        if not counter_names:
            return

        thread_ids = _list_task_thread_ids(target_tgid)
        started_specs: list[_CounterSpec] = []
        try:
            for counter_name in counter_names:
                spec = _COUNTER_SPECS[counter_name]
                self._start_counter(spec, target_tgid, thread_ids=thread_ids)
                started_specs.append(spec)
        except Exception:
            for spec in reversed(started_specs):
                self._close_active_counter(spec)
            raise

    def stop_counters(self, counter_names: list[str]) -> dict[str, int]:
        """Stop the configured replay counters and return their final totals.

        Example output:
            {"dtlb_loads": 300, "dtlb_misses": 7}

        Args:
            counter_names: Replay counters that were started for this timed window.

        Returns:
            Mapping from stable replay counter name to final cumulative total.

        """
        totals: dict[str, int] = {}
        try:
            for counter_name in counter_names:
                spec = _COUNTER_SPECS[counter_name]
                totals[spec.name] = self._stop_counter(spec)
        except Exception:
            for counter_name in counter_names:
                spec = _COUNTER_SPECS.get(counter_name)
                if spec is not None:
                    self._close_active_counter(spec)
            raise
        return totals

    def close(self) -> None:
        """Best-effort cleanup for all active counter sessions."""
        for session in list(self._active_counters.values()):
            session.close()
        self._active_counters.clear()

    def _start_counter(
        self,
        spec: _CounterSpec,
        target_tgid: int,
        *,
        thread_ids: list[int] | None = None,
    ) -> None:
        """Start one replay counter for the Redis thread set."""
        if spec.name in self._active_counters:
            raise RuntimeError(f"{spec.name} collection is already active.")

        if thread_ids is None:
            thread_ids = _list_task_thread_ids(target_tgid)

        session = _PerfCounterSession.start(
            spec=spec,
            target_tgid=target_tgid,
            thread_ids=thread_ids,
        )
        self._active_counters[spec.name] = session

    def _stop_counter(self, spec: _CounterSpec) -> int:
        """Stop one active replay counter and return its total."""
        session = self._active_counters.pop(spec.name, None)
        if session is None:
            raise RuntimeError(f"{spec.name} collection was not started.")
        return session.stop()

    def _close_active_counter(self, spec: _CounterSpec) -> None:
        """Discard one active replay counter without reading a final total."""
        session = self._active_counters.pop(spec.name, None)
        if session is None:
            return
        session.close()

__all__ = [
    "DTLBCounterMetrics",
    "HardwareCollector",
]

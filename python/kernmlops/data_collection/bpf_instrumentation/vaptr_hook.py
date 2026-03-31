import subprocess
import sys
import time
from dataclasses import dataclass

import polars as pl
from data_collection.bpf_instrumentation.bpf_hook import BPFProgram
from data_collection.page_access import (
    PageAccessRequest,
    PageAccessResult,
    PageAccessTracker,
    ResolvedPhysicalPage,
)
from data_schema.schema import CollectionTable
from data_schema.vaptr import VAPtrTable


@dataclass(frozen=True)
class VAPtrData:
    ts_uptime_us: int
    key: str
    address: str
    page_addr: str
    available: bool
    mapped_pfn: int | None
    tracking_pfn: int | None
    physical_page_addr: int | None
    tracking_physical_page_addr: int | None
    page_idle: bool | None
    access_bit: bool | None
    access_bit_valid: bool


@dataclass(frozen=True)
class PendingVAPtrSample:
    key: str
    address: str
    page_addr: str
    available: bool
    resolved_page: ResolvedPhysicalPage | None


class VAPtrHook(BPFProgram):
    @classmethod
    def name(cls) -> str:
        return "vaptr"

    def __init__(
        self,
        num_keys: int = 10,
        field_name: str = "field0",
        key_names: list[str] | None = None,
        page_access_tracker: PageAccessTracker | None = None,
    ):
        self.num_keys = num_keys
        self.field_name = field_name
        self.collection_id = ""
        self.samples = list[VAPtrData]()
        self.key_names = list(key_names or [])
        self.module_verified = False
        self.redis_pid: int | None = None
        self.page_access_tracker = page_access_tracker or PageAccessTracker()
        self.pending_samples = list[PendingVAPtrSample]()

    def load(self, collection_id: str):
        self.collection_id = collection_id

    def _discover_keys(self) -> list[str]:
        if self.num_keys <= 0:
            return []

        seen = set[str]()
        cursor = "0"

        while True:
            cmd = [
                "redis-cli",
                "--raw",
                "SCAN",
                cursor,
                "MATCH",
                "user*",
                "COUNT",
                str(max(self.num_keys * 2, 10)),
            ]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return []

            if result.returncode != 0:
                return []

            lines = [
                line.strip() for line in result.stdout.splitlines() if line.strip()
            ]
            if not lines:
                return []

            cursor = lines[0]
            for key_name in lines[1:]:
                if key_name in seen:
                    continue
                seen.add(key_name)
                if len(seen) >= self.num_keys:
                    return sorted(seen)

            if cursor == "0":
                return sorted(seen)

    def _run_vaptr(self) -> subprocess.CompletedProcess[str] | None:
        cmd = ["redis-cli", "--raw", "VAPTR", "FIELD", self.field_name] + self.key_names
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def _discover_redis_pid(self) -> int | None:
        try:
            result = subprocess.run(
                ["redis-cli", "--raw", "INFO", "server"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            if not line.startswith("process_id:"):
                continue
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
        return None

    def _redis_pid_alive(self, pid: int) -> bool:
        try:
            return self.page_access_tracker.proc_root.joinpath(str(pid)).is_dir()
        except OSError:
            return False

    def _invalidate_redis_pid(self) -> None:
        if self.redis_pid is None:
            return
        self.page_access_tracker.invalidate_pid(self.redis_pid)
        self.redis_pid = None

    def _get_redis_pid(self) -> int | None:
        if self.redis_pid is not None:
            if self._redis_pid_alive(self.redis_pid):
                return self.redis_pid
            self._invalidate_redis_pid()

        self.redis_pid = self._discover_redis_pid()
        return self.redis_pid

    def _resolve_page_access(
        self,
        page_requests: list[tuple[int, int]],
    ) -> list[ResolvedPhysicalPage | None]:
        if not page_requests:
            return []

        redis_pid = self._get_redis_pid()
        if redis_pid is None:
            return [None] * len(page_requests)

        requests = [
            PageAccessRequest(pid=redis_pid, virtual_address=page_addr_int)
            for _, page_addr_int in page_requests
        ]

        try:
            return self.page_access_tracker.resolve_many(requests)
        except (OSError, ProcessLookupError):
            self._invalidate_redis_pid()

        redis_pid = self._get_redis_pid()
        if redis_pid is None:
            return [None] * len(page_requests)

        requests = [
            PageAccessRequest(pid=redis_pid, virtual_address=page_addr_int)
            for _, page_addr_int in page_requests
        ]
        try:
            return self.page_access_tracker.resolve_many(requests)
        except (OSError, ProcessLookupError):
            self._invalidate_redis_pid()
            return [None] * len(page_requests)

    def _append_sample_row(
        self,
        *,
        ts_uptime_us: int,
        key_name: str,
        addr_str: str,
        page_addr: str,
        available: bool,
        access_result: PageAccessResult | None,
    ) -> None:
        self.samples.append(
            VAPtrData(
                ts_uptime_us=ts_uptime_us,
                key=key_name,
                address=addr_str,
                page_addr=page_addr,
                available=available,
                mapped_pfn=None if access_result is None else access_result.mapped_pfn,
                tracking_pfn=None
                if access_result is None
                else access_result.tracking_pfn,
                physical_page_addr=None
                if access_result is None
                else access_result.physical_page_addr,
                tracking_physical_page_addr=None
                if access_result is None
                else access_result.tracking_physical_page_addr,
                page_idle=None if access_result is None else access_result.page_idle,
                access_bit=None if access_result is None else access_result.access_bit,
                access_bit_valid=False
                if access_result is None
                else access_result.access_bit_valid,
            )
        )

    def _record_pending_samples(self, ts_uptime_us: int) -> None:
        if not self.pending_samples:
            return

        resolved_pages = [
            pending_sample.resolved_page
            for pending_sample in self.pending_samples
            if pending_sample.resolved_page is not None
        ]
        access_results = iter(self.page_access_tracker.read_many(resolved_pages))
        for pending_sample in self.pending_samples:
            access_result = (
                next(access_results)
                if pending_sample.resolved_page is not None
                else None
            )
            self._append_sample_row(
                ts_uptime_us=ts_uptime_us,
                key_name=pending_sample.key,
                addr_str=pending_sample.address,
                page_addr=pending_sample.page_addr,
                available=pending_sample.available,
                access_result=access_result,
            )

    def _parse_vaptr_output(
        self,
        output: str,
    ) -> list[tuple[str, str, str, bool, int | None]]:
        lines = output.splitlines()
        raw_samples = list[tuple[str, str, str, bool, int | None]]()

        for i in range(0, len(lines) - 1, 2):
            key_name = lines[i].strip()
            addr_str = lines[i + 1].strip()
            available = addr_str.startswith("0x")
            page_addr_int: int | None = None
            if available:
                try:
                    addr_int = int(addr_str, 16)
                except ValueError:
                    available = False
                    addr_str = "n/a"
                    page_addr = "n/a"
                else:
                    page_addr_int = addr_int & ~(self.page_access_tracker.page_size - 1)
                    page_addr = f"0x{page_addr_int:x}"
            else:
                addr_str = "n/a"
                page_addr = "n/a"
            raw_samples.append(
                (key_name, addr_str, page_addr, available, page_addr_int)
            )
        return raw_samples

    def _prepare_pending_samples(
        self,
        raw_samples: list[tuple[str, str, str, bool, int | None]],
    ) -> list[PendingVAPtrSample]:
        page_requests = list[tuple[int, int]]()
        for sample_index, (_, _, _, available, page_addr_int) in enumerate(raw_samples):
            if available and page_addr_int is not None:
                page_requests.append((sample_index, page_addr_int))

        resolved_pages = self._resolve_page_access(page_requests)
        resolved_by_index = {
            sample_index: resolved_page
            for (sample_index, _), resolved_page in zip(page_requests, resolved_pages)
        }

        pending_samples = list[PendingVAPtrSample]()
        for sample_index, (
            key_name,
            addr_str,
            page_addr,
            available,
            _page_addr_int,
        ) in enumerate(raw_samples):
            pending_samples.append(
                PendingVAPtrSample(
                    key=key_name,
                    address=addr_str,
                    page_addr=page_addr,
                    available=available,
                    resolved_page=resolved_by_index.get(sample_index),
                )
            )
        return pending_samples

    def _arm_pending_samples(self, pending_samples: list[PendingVAPtrSample]) -> None:
        resolved_pages = [
            pending_sample.resolved_page
            for pending_sample in pending_samples
            if pending_sample.resolved_page is not None
        ]
        self.page_access_tracker.arm_many(resolved_pages)

    def poll(self):
        if not self.key_names:
            self.key_names = self._discover_keys()
        if not self.key_names:
            return

        ts_uptime_us = int(time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1000)
        had_pending_samples = bool(self.pending_samples)
        self._record_pending_samples(ts_uptime_us)
        self.pending_samples.clear()

        result = self._run_vaptr()
        if result is None:
            return

        output = result.stdout.strip()

        if not self.module_verified:
            if (
                "ERR unknown command" in output
                or "ERR unknown command" in result.stderr
            ):
                print(
                    "vaptr: ERROR - VAPTR command not recognized by redis-server. "
                    "Is the vaptr.so module loaded? The redis benchmark now auto-builds "
                    "and loads ./redis-module/vaptr.so; verify that benchmark-managed "
                    "redis-server (not system redis) is the one running on port 6379.",
                    file=sys.stderr,
                )
                return
            if output:
                self.module_verified = True

        if result.returncode != 0:
            return

        if not output:
            return

        raw_samples = self._parse_vaptr_output(output)
        pending_samples = self._prepare_pending_samples(raw_samples)
        self._arm_pending_samples(pending_samples)
        self.pending_samples = pending_samples

        if not had_pending_samples:
            for pending_sample in pending_samples:
                self._append_sample_row(
                    ts_uptime_us=ts_uptime_us,
                    key_name=pending_sample.key,
                    addr_str=pending_sample.address,
                    page_addr=pending_sample.page_addr,
                    available=pending_sample.available,
                    access_result=None,
                )

    def close(self):
        self.page_access_tracker.close()

    def data(self) -> list[CollectionTable]:
        if not self.samples:
            return []
        return [
            VAPtrTable.from_df_id(
                pl.DataFrame(self.samples, schema=VAPtrTable.schema()),
                collection_id=self.collection_id,
            )
        ]

    def clear(self):
        self.samples.clear()

    def pop_data(self) -> list[CollectionTable]:
        tables = self.data()
        self.clear()
        return tables

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import polars as pl
from data_collection.bpf_instrumentation.bpf_hook import BPFProgram
from data_schema import CollectionTable
from data_schema.thp_harness import VMStatSampleTable


VMSTAT_KEYS = (
    "pgfault",
    "pgmajfault",
    "pgmigrate_success",
    "pgmigrate_fail",
    "thp_migration_success",
    "thp_migration_fail",
    "thp_migration_split",
    "compact_migrate_scanned",
    "compact_free_scanned",
    "compact_isolated",
    "compact_stall",
    "compact_fail",
    "compact_success",
    "compact_daemon_wake",
    "thp_fault_alloc",
    "thp_fault_fallback",
    "thp_collapse_alloc",
    "thp_collapse_alloc_failed",
    "thp_split_page",
    "thp_split_page_failed",
    "thp_deferred_split_page",
    "thp_split_pmd",
)


@dataclass(frozen=True)
class VMStatSample:
    ts_ns: int
    pgfault: int
    pgmajfault: int
    pgmigrate_success: int
    pgmigrate_fail: int
    thp_migration_success: int
    thp_migration_fail: int
    thp_migration_split: int
    compact_migrate_scanned: int
    compact_free_scanned: int
    compact_isolated: int
    compact_stall: int
    compact_fail: int
    compact_success: int
    compact_daemon_wake: int
    thp_fault_alloc: int
    thp_fault_fallback: int
    thp_collapse_alloc: int
    thp_collapse_alloc_failed: int
    thp_split_page: int
    thp_split_page_failed: int
    thp_deferred_split_page: int
    thp_split_pmd: int


class VMStatHarnessHook(BPFProgram):
    @classmethod
    def name(cls) -> str:
        return "vmstat_harness"

    @classmethod
    def _proc_vmstat(cls) -> Path:
        return Path("/proc/vmstat")

    def __init__(self, hugepage_harness=None):
        self.samples = list[VMStatSample]()
        self.last_sample_ns = 0
        sample_ms = 200
        if hugepage_harness is not None:
            sample_ms = int(getattr(hugepage_harness, "vmstat_interval_ms", 200))
        self.sample_interval_ns = int(sample_ms * 1e6)

    def load(self, collection_id: str):
        self.collection_id = collection_id

    def _parse_vmstat(self) -> Mapping[str, int]:
        values = dict[str, int]()
        for line in self._proc_vmstat().read_text().splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            if parts[0] in VMSTAT_KEYS:
                values[parts[0]] = int(parts[1])
        return values

    def poll(self):
        now = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        if now - self.last_sample_ns < self.sample_interval_ns:
            return
        self.last_sample_ns = now
        values = self._parse_vmstat()
        self.samples.append(
            VMStatSample(
                ts_ns=now,
                pgfault=values.get("pgfault", 0),
                pgmajfault=values.get("pgmajfault", 0),
                pgmigrate_success=values.get("pgmigrate_success", 0),
                pgmigrate_fail=values.get("pgmigrate_fail", 0),
                thp_migration_success=values.get("thp_migration_success", 0),
                thp_migration_fail=values.get("thp_migration_fail", 0),
                thp_migration_split=values.get("thp_migration_split", 0),
                compact_migrate_scanned=values.get("compact_migrate_scanned", 0),
                compact_free_scanned=values.get("compact_free_scanned", 0),
                compact_isolated=values.get("compact_isolated", 0),
                compact_stall=values.get("compact_stall", 0),
                compact_fail=values.get("compact_fail", 0),
                compact_success=values.get("compact_success", 0),
                compact_daemon_wake=values.get("compact_daemon_wake", 0),
                thp_fault_alloc=values.get("thp_fault_alloc", 0),
                thp_fault_fallback=values.get("thp_fault_fallback", 0),
                thp_collapse_alloc=values.get("thp_collapse_alloc", 0),
                thp_collapse_alloc_failed=values.get("thp_collapse_alloc_failed", 0),
                thp_split_page=values.get("thp_split_page", 0),
                thp_split_page_failed=values.get("thp_split_page_failed", 0),
                thp_deferred_split_page=values.get("thp_deferred_split_page", 0),
                thp_split_pmd=values.get("thp_split_pmd", 0),
            )
        )

    def close(self):
        pass

    def data(self) -> list[CollectionTable]:
        if not self.samples:
            return []
        return [
            VMStatSampleTable.from_df_id(
                pl.DataFrame(self.samples), collection_id=self.collection_id
            )
        ]

    def clear(self):
        self.samples.clear()

    def pop_data(self) -> list[CollectionTable]:
        tables = self.data()
        self.clear()
        return tables

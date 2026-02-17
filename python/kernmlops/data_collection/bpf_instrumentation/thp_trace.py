from dataclasses import dataclass
from pathlib import Path

import polars as pl
from bcc import BPF
from data_collection.bpf_instrumentation.bpf_hook import POLL_TIMEOUT_MS, BPFProgram
from data_schema import CollectionTable
from data_schema.thp_harness import (
    THPCollapseEventTable,
    THPCollapseIsolateEventTable,
    THPCollapseSwapinEventTable,
    THPCompactionEventTable,
    THPMigrateEventTable,
    THPScanEventTable,
    THPSyscallEventTable,
    THPTLBFlushTraceTable,
)


COMPACTION_EVENT_NAMES = {
    1: "mm_compaction_suitable",
    2: "mm_compaction_finished",
    3: "mm_compaction_begin",
    4: "mm_compaction_end",
    5: "mm_compaction_migratepages",
    6: "mm_compaction_isolate_freepages",
    7: "mm_compaction_isolate_migratepages",
    8: "mm_compaction_try_to_compact_pages",
    9: "mm_compaction_deferred",
    10: "mm_compaction_defer_compaction",
    11: "mm_compaction_kcompactd_wake",
    12: "mm_compaction_kcompactd_sleep",
    13: "mm_compaction_wakeup_kcompactd",
}

MIGRATE_EVENT_NAMES = {
    1: "mm_migrate_pages_start",
    2: "mm_migrate_pages",
}

SYSCALL_NAMES = {
    1: "madvise",
    2: "munmap",
}

SYSCALL_PHASES = {
    0: "enter",
    1: "exit",
}


@dataclass(frozen=True)
class THPScanEvent:
    pid: int
    tgid: int
    ts_ns: int
    mm: str
    pfn: int
    writable: int
    referenced: int
    none_or_zero: int
    status: int
    unmapped: int


@dataclass(frozen=True)
class THPCollapseEvent:
    pid: int
    tgid: int
    ts_ns: int
    mm: str
    isolated: int
    status: int


@dataclass(frozen=True)
class THPCollapseIsolateEvent:
    pid: int
    tgid: int
    ts_ns: int
    pfn: int
    none_or_zero: int
    referenced: int
    writable: int
    status: int


@dataclass(frozen=True)
class THPCollapseSwapinEvent:
    pid: int
    tgid: int
    ts_ns: int
    mm: str
    swapped_in: int
    referenced: int
    ret: int


@dataclass(frozen=True)
class THPCompactionEvent:
    pid: int
    tgid: int
    ts_ns: int
    event_name: str
    nid: int
    zone_idx: int
    order: int
    ret: int
    status: int
    sync: int
    start_pfn: int
    end_pfn: int
    nr_scanned: int
    nr_taken: int
    nr_migrated: int
    nr_failed: int
    considered: int
    defer_shift: int
    order_failed: int
    zone_start: int
    migrate_pfn: int
    free_pfn: int
    zone_end: int
    gfp_mask: int
    prio: int


@dataclass(frozen=True)
class THPMigrateEvent:
    pid: int
    tgid: int
    ts_ns: int
    event_name: str
    succeeded: int
    failed: int
    thp_succeeded: int
    thp_failed: int
    thp_split: int
    mode: int
    reason: int


@dataclass(frozen=True)
class THPTLBFlushEvent:
    pid: int
    tgid: int
    ts_ns: int
    reason: int
    pages: int


@dataclass(frozen=True)
class THPSyscallEvent:
    pid: int
    tgid: int
    ts_ns: int
    syscall: str
    phase: str
    address: int
    length: int
    behavior: int
    ret: int


class THPTraceBPFHook(BPFProgram):
    @classmethod
    def name(cls) -> str:
        return "thp_trace"

    def __init__(self):
        self.bpf_text = open(Path(__file__).parent / "bpf/thp_trace.bpf.c", "r").read()
        self.thp_scan_events = list[THPScanEvent]()
        self.thp_collapse_events = list[THPCollapseEvent]()
        self.thp_collapse_isolate_events = list[THPCollapseIsolateEvent]()
        self.thp_collapse_swapin_events = list[THPCollapseSwapinEvent]()
        self.thp_compaction_events = list[THPCompactionEvent]()
        self.thp_migrate_events = list[THPMigrateEvent]()
        self.thp_tlb_flush_events = list[THPTLBFlushEvent]()
        self.thp_syscall_events = list[THPSyscallEvent]()

    def load(self, collection_id: str):
        self.collection_id = collection_id
        self.bpf = BPF(text=self.bpf_text)
        self.bpf["thp_scan_events"].open_perf_buffer(self._thp_scan_eh, page_cnt=128)
        self.bpf["thp_collapse_events"].open_perf_buffer(
            self._thp_collapse_eh, page_cnt=128
        )
        self.bpf["thp_collapse_isolate_events"].open_perf_buffer(
            self._thp_collapse_isolate_eh, page_cnt=128
        )
        self.bpf["thp_collapse_swapin_events"].open_perf_buffer(
            self._thp_collapse_swapin_eh, page_cnt=128
        )
        self.bpf["thp_compaction_events"].open_perf_buffer(
            self._thp_compaction_eh, page_cnt=128
        )
        self.bpf["thp_migrate_events"].open_perf_buffer(
            self._thp_migrate_eh, page_cnt=128
        )
        self.bpf["thp_tlb_flush_events"].open_perf_buffer(
            self._thp_tlb_flush_eh, page_cnt=128
        )
        self.bpf["thp_syscall_events"].open_perf_buffer(
            self._thp_syscall_eh, page_cnt=128
        )

    def poll(self):
        self.bpf.perf_buffer_poll(timeout=POLL_TIMEOUT_MS)

    def close(self):
        self.bpf.cleanup()

    def data(self) -> list[CollectionTable]:
        tables: list[CollectionTable] = []
        scan_schema = pl.Schema(
            {
                k: v
                for k, v in THPScanEventTable.schema().items()
                if k != "collection_id"
            }
        )
        collapse_schema = pl.Schema(
            {
                k: v
                for k, v in THPCollapseEventTable.schema().items()
                if k != "collection_id"
            }
        )
        collapse_isolate_schema = pl.Schema(
            {
                k: v
                for k, v in THPCollapseIsolateEventTable.schema().items()
                if k != "collection_id"
            }
        )
        collapse_swapin_schema = pl.Schema(
            {
                k: v
                for k, v in THPCollapseSwapinEventTable.schema().items()
                if k != "collection_id"
            }
        )
        compaction_schema = pl.Schema(
            {
                k: v
                for k, v in THPCompactionEventTable.schema().items()
                if k != "collection_id"
            }
        )
        migrate_schema = pl.Schema(
            {
                k: v
                for k, v in THPMigrateEventTable.schema().items()
                if k != "collection_id"
            }
        )
        tlb_schema = pl.Schema(
            {
                k: v
                for k, v in THPTLBFlushTraceTable.schema().items()
                if k != "collection_id"
            }
        )
        syscall_schema = pl.Schema(
            {
                k: v
                for k, v in THPSyscallEventTable.schema().items()
                if k != "collection_id"
            }
        )
        if self.thp_scan_events:
            tables.append(
                THPScanEventTable.from_df_id(
                    pl.DataFrame(self.thp_scan_events, schema=scan_schema),
                    collection_id=self.collection_id,
                )
            )
        if self.thp_collapse_events:
            tables.append(
                THPCollapseEventTable.from_df_id(
                    pl.DataFrame(self.thp_collapse_events, schema=collapse_schema),
                    collection_id=self.collection_id,
                )
            )
        if self.thp_collapse_isolate_events:
            tables.append(
                THPCollapseIsolateEventTable.from_df_id(
                    pl.DataFrame(
                        self.thp_collapse_isolate_events, schema=collapse_isolate_schema
                    ),
                    collection_id=self.collection_id,
                )
            )
        if self.thp_collapse_swapin_events:
            tables.append(
                THPCollapseSwapinEventTable.from_df_id(
                    pl.DataFrame(
                        self.thp_collapse_swapin_events, schema=collapse_swapin_schema
                    ),
                    collection_id=self.collection_id,
                )
            )
        if self.thp_compaction_events:
            tables.append(
                THPCompactionEventTable.from_df_id(
                    pl.DataFrame(self.thp_compaction_events, schema=compaction_schema),
                    collection_id=self.collection_id,
                )
            )
        if self.thp_migrate_events:
            tables.append(
                THPMigrateEventTable.from_df_id(
                    pl.DataFrame(self.thp_migrate_events, schema=migrate_schema),
                    collection_id=self.collection_id,
                )
            )
        if self.thp_tlb_flush_events:
            tables.append(
                THPTLBFlushTraceTable.from_df_id(
                    pl.DataFrame(self.thp_tlb_flush_events, schema=tlb_schema),
                    collection_id=self.collection_id,
                )
            )
        if self.thp_syscall_events:
            tables.append(
                THPSyscallEventTable.from_df_id(
                    pl.DataFrame(self.thp_syscall_events, schema=syscall_schema),
                    collection_id=self.collection_id,
                )
            )
        return tables

    def clear(self):
        self.thp_scan_events.clear()
        self.thp_collapse_events.clear()
        self.thp_collapse_isolate_events.clear()
        self.thp_collapse_swapin_events.clear()
        self.thp_compaction_events.clear()
        self.thp_migrate_events.clear()
        self.thp_tlb_flush_events.clear()
        self.thp_syscall_events.clear()

    def pop_data(self) -> list[CollectionTable]:
        data = self.data()
        self.clear()
        return data

    def _thp_scan_eh(self, cpu, data, size):
        event = self.bpf["thp_scan_events"].event(data)
        self.thp_scan_events.append(
            THPScanEvent(
                pid=event.pid,
                tgid=event.tgid,
                ts_ns=event.ts_ns,
                mm=hex(event.mm),
                pfn=event.pfn,
                writable=event.writable,
                referenced=event.referenced,
                none_or_zero=event.none_or_zero,
                status=event.status,
                unmapped=event.unmapped,
            )
        )

    def _thp_collapse_eh(self, cpu, data, size):
        event = self.bpf["thp_collapse_events"].event(data)
        self.thp_collapse_events.append(
            THPCollapseEvent(
                pid=event.pid,
                tgid=event.tgid,
                ts_ns=event.ts_ns,
                mm=hex(event.mm),
                isolated=event.isolated,
                status=event.status,
            )
        )

    def _thp_collapse_isolate_eh(self, cpu, data, size):
        event = self.bpf["thp_collapse_isolate_events"].event(data)
        self.thp_collapse_isolate_events.append(
            THPCollapseIsolateEvent(
                pid=event.pid,
                tgid=event.tgid,
                ts_ns=event.ts_ns,
                pfn=event.pfn,
                none_or_zero=event.none_or_zero,
                referenced=event.referenced,
                writable=event.writable,
                status=event.status,
            )
        )

    def _thp_collapse_swapin_eh(self, cpu, data, size):
        event = self.bpf["thp_collapse_swapin_events"].event(data)
        self.thp_collapse_swapin_events.append(
            THPCollapseSwapinEvent(
                pid=event.pid,
                tgid=event.tgid,
                ts_ns=event.ts_ns,
                mm=hex(event.mm),
                swapped_in=event.swapped_in,
                referenced=event.referenced,
                ret=event.ret,
            )
        )

    def _thp_compaction_eh(self, cpu, data, size):
        event = self.bpf["thp_compaction_events"].event(data)
        self.thp_compaction_events.append(
            THPCompactionEvent(
                pid=event.pid,
                tgid=event.tgid,
                ts_ns=event.ts_ns,
                event_name=COMPACTION_EVENT_NAMES.get(event.event_id, "unknown"),
                nid=event.nid,
                zone_idx=event.zone_idx,
                order=event.order,
                ret=event.ret,
                status=event.status,
                sync=event.sync,
                start_pfn=event.start_pfn,
                end_pfn=event.end_pfn,
                nr_scanned=event.nr_scanned,
                nr_taken=event.nr_taken,
                nr_migrated=event.nr_migrated,
                nr_failed=event.nr_failed,
                considered=event.considered,
                defer_shift=event.defer_shift,
                order_failed=event.order_failed,
                zone_start=event.zone_start,
                migrate_pfn=event.migrate_pfn,
                free_pfn=event.free_pfn,
                zone_end=event.zone_end,
                gfp_mask=event.gfp_mask,
                prio=event.prio,
            )
        )

    def _thp_migrate_eh(self, cpu, data, size):
        event = self.bpf["thp_migrate_events"].event(data)
        self.thp_migrate_events.append(
            THPMigrateEvent(
                pid=event.pid,
                tgid=event.tgid,
                ts_ns=event.ts_ns,
                event_name=MIGRATE_EVENT_NAMES.get(event.event_id, "unknown"),
                succeeded=event.succeeded,
                failed=event.failed,
                thp_succeeded=event.thp_succeeded,
                thp_failed=event.thp_failed,
                thp_split=event.thp_split,
                mode=event.mode,
                reason=event.reason,
            )
        )

    def _thp_tlb_flush_eh(self, cpu, data, size):
        event = self.bpf["thp_tlb_flush_events"].event(data)
        self.thp_tlb_flush_events.append(
            THPTLBFlushEvent(
                pid=event.pid,
                tgid=event.tgid,
                ts_ns=event.ts_ns,
                reason=event.reason,
                pages=event.pages,
            )
        )

    def _thp_syscall_eh(self, cpu, data, size):
        event = self.bpf["thp_syscall_events"].event(data)
        self.thp_syscall_events.append(
            THPSyscallEvent(
                pid=event.pid,
                tgid=event.tgid,
                ts_ns=event.ts_ns,
                syscall=SYSCALL_NAMES.get(event.syscall_id, "unknown"),
                phase=SYSCALL_PHASES.get(event.phase, "unknown"),
                address=event.address,
                length=event.length,
                behavior=event.behavior,
                ret=event.ret,
            )
        )

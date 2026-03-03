from __future__ import annotations

from dataclasses import dataclass

import polars as pl
from data_schema.schema import (
    CollectionGraph,
    CollectionTable,
    GraphEngine,
)


class _BaseTHPTable(CollectionTable):
    _table_name: str = ""
    _table_schema: pl.Schema = pl.Schema()
    _graph_types: list[type[CollectionGraph]] = []

    @classmethod
    def name(cls) -> str:
        return cls._table_name

    @classmethod
    def schema(cls) -> pl.Schema:
        return cls._table_schema

    @classmethod
    def from_df(cls, table: pl.DataFrame) -> "_BaseTHPTable":
        return cls(table=table.cast(cls.schema(), strict=False))

    def __init__(self, table: pl.DataFrame):
        self._table = table

    @property
    def table(self) -> pl.DataFrame:
        return self._table

    def filtered_table(self) -> pl.DataFrame:
        return self.table

    def graphs(self) -> list[type[CollectionGraph]]:
        return self._graph_types


class THPScanEventTable(_BaseTHPTable):
    _table_name = "thp_scan_events"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "mm": pl.String(),
            "pfn": pl.UInt64(),
            "writable": pl.Int64(),
            "referenced": pl.Int64(),
            "none_or_zero": pl.Int64(),
            "status": pl.Int64(),
            "unmapped": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class THPCollapseEventTable(_BaseTHPTable):
    _table_name = "thp_collapse_events"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "mm": pl.String(),
            "isolated": pl.Int64(),
            "status": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class THPCollapseIsolateEventTable(_BaseTHPTable):
    _table_name = "thp_collapse_isolate_events"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "pfn": pl.UInt64(),
            "none_or_zero": pl.Int64(),
            "referenced": pl.Int64(),
            "writable": pl.Int64(),
            "status": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class THPCollapseSwapinEventTable(_BaseTHPTable):
    _table_name = "thp_collapse_swapin_events"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "mm": pl.String(),
            "swapped_in": pl.Int64(),
            "referenced": pl.Int64(),
            "ret": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class THPCompactionEventTable(_BaseTHPTable):
    _table_name = "thp_compaction_events"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "event_name": pl.String(),
            "nid": pl.Int64(),
            "zone_idx": pl.Int64(),
            "order": pl.Int64(),
            "ret": pl.Int64(),
            "status": pl.Int64(),
            "sync": pl.Int64(),
            "start_pfn": pl.UInt64(),
            "end_pfn": pl.UInt64(),
            "nr_scanned": pl.UInt64(),
            "nr_taken": pl.UInt64(),
            "nr_migrated": pl.UInt64(),
            "nr_failed": pl.UInt64(),
            "considered": pl.UInt64(),
            "defer_shift": pl.UInt64(),
            "order_failed": pl.Int64(),
            "zone_start": pl.UInt64(),
            "migrate_pfn": pl.UInt64(),
            "free_pfn": pl.UInt64(),
            "zone_end": pl.UInt64(),
            "gfp_mask": pl.UInt64(),
            "prio": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class THPMigrateEventTable(_BaseTHPTable):
    _table_name = "thp_migrate_events"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "event_name": pl.String(),
            "succeeded": pl.UInt64(),
            "failed": pl.UInt64(),
            "thp_succeeded": pl.UInt64(),
            "thp_failed": pl.UInt64(),
            "thp_split": pl.UInt64(),
            "mode": pl.Int64(),
            "reason": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class THPTLBFlushTraceTable(_BaseTHPTable):
    _table_name = "thp_tlb_flush_trace_events"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "reason": pl.Int64(),
            "pages": pl.UInt64(),
            "collection_id": pl.String(),
        }
    )


class THPSyscallEventTable(_BaseTHPTable):
    _table_name = "thp_syscall_events"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "syscall": pl.String(),
            "phase": pl.String(),
            "address": pl.UInt64(),
            "length": pl.UInt64(),
            "behavior": pl.Int64(),
            "ret": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class VMStatSampleTable(_BaseTHPTable):
    _table_name = "vmstat_samples"
    _table_schema = pl.Schema(
        {
            "ts_ns": pl.Int64(),
            "pgfault": pl.Int64(),
            "pgmajfault": pl.Int64(),
            "pgmigrate_success": pl.Int64(),
            "pgmigrate_fail": pl.Int64(),
            "thp_migration_success": pl.Int64(),
            "thp_migration_fail": pl.Int64(),
            "thp_migration_split": pl.Int64(),
            "compact_migrate_scanned": pl.Int64(),
            "compact_free_scanned": pl.Int64(),
            "compact_isolated": pl.Int64(),
            "compact_stall": pl.Int64(),
            "compact_fail": pl.Int64(),
            "compact_success": pl.Int64(),
            "compact_daemon_wake": pl.Int64(),
            "thp_fault_alloc": pl.Int64(),
            "thp_fault_fallback": pl.Int64(),
            "thp_collapse_alloc": pl.Int64(),
            "thp_collapse_alloc_failed": pl.Int64(),
            "thp_split_page": pl.Int64(),
            "thp_split_page_failed": pl.Int64(),
            "thp_deferred_split_page": pl.Int64(),
            "thp_split_pmd": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class SmapsRollupSampleTable(_BaseTHPTable):
    _table_name = "smaps_rollup_samples"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "rss_kb": pl.Int64(),
            "pss_kb": pl.Int64(),
            "anonymous_kb": pl.Int64(),
            "referenced_kb": pl.Int64(),
            "anon_hugepages_kb": pl.Int64(),
            "ra_pages_kb": pl.Int64(),
            "ra_state": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class SmapsVMARegionSampleTable(_BaseTHPTable):
    _table_name = "smaps_vma_samples"
    _table_schema = pl.Schema(
        {
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "start_addr": pl.UInt64(),
            "end_addr": pl.UInt64(),
            "rss_kb": pl.Int64(),
            "anonymous_kb": pl.Int64(),
            "referenced_kb": pl.Int64(),
            "anon_hugepages_kb": pl.Int64(),
            "thp_eligible": pl.Int64(),
            "ra_pages_kb": pl.Int64(),
            "ra_state": pl.Int64(),
            "vm_flags": pl.String(),
            "collection_id": pl.String(),
        }
    )


class THPCandidateTable(_BaseTHPTable):
    _table_name = "thp_candidates"
    _table_schema = pl.Schema(
        {
            "decision_id": pl.String(),
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "candidate_type": pl.String(),
            "mm": pl.String(),
            "pfn": pl.UInt64(),
            "start_addr": pl.UInt64(),
            "end_addr": pl.UInt64(),
            "age_sec": pl.Float64(),
            "age_censored": pl.Boolean(),
            "referenced": pl.Int64(),
            "none_or_zero": pl.Int64(),
            "writable": pl.Int64(),
            "scan_status": pl.Int64(),
            "anon_hugepages_kb": pl.Int64(),
            "thp_eligible": pl.Int64(),
            "recent_scan_hit": pl.Int64(),
            "collection_id": pl.String(),
        }
    )


class THPInterventionEventTable(_BaseTHPTable):
    _table_name = "thp_interventions"
    _table_schema = pl.Schema(
        {
            "intervention_id": pl.String(),
            "decision_id": pl.String(),
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "start_addr": pl.UInt64(),
            "end_addr": pl.UInt64(),
            "age_bucket": pl.String(),
            "success": pl.Boolean(),
            "error": pl.String(),
            "command": pl.String(),
            "collection_id": pl.String(),
        }
    )


class THPWindowFeatures100msTable(_BaseTHPTable):
    _table_name = "thp_window_features_100ms"
    _table_schema = pl.Schema(
        {
            "decision_id": pl.String(),
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "window_ms": pl.Int64(),
            "instructions_pre": pl.Float64(),
            "instructions_post": pl.Float64(),
            "dtlb_miss_pre": pl.Float64(),
            "dtlb_miss_post": pl.Float64(),
            "walk_pre": pl.Float64(),
            "walk_post": pl.Float64(),
            "tlb_flush_pre": pl.Float64(),
            "tlb_flush_post": pl.Float64(),
            "miss_rate_pre": pl.Float64(),
            "miss_rate_post": pl.Float64(),
            "walk_rate_pre": pl.Float64(),
            "walk_rate_post": pl.Float64(),
            "tlb_flush_rate_pre": pl.Float64(),
            "tlb_flush_rate_post": pl.Float64(),
            "delta_miss_rate": pl.Float64(),
            "delta_walk_rate": pl.Float64(),
            "delta_tlb_flush_rate": pl.Float64(),
            "collection_id": pl.String(),
        }
    )


class THPWindowFeatures1sTable(THPWindowFeatures100msTable):
    _table_name = "thp_window_features_1s"


class THPWindowFeatures10sTable(THPWindowFeatures100msTable):
    _table_name = "thp_window_features_10s"


class THPInterventionMatchTable(_BaseTHPTable):
    _table_name = "thp_intervention_match"
    _table_schema = pl.Schema(
        {
            "intervention_id": pl.String(),
            "decision_id_treated": pl.String(),
            "decision_id_control": pl.String(),
            "distance": pl.Float64(),
            "age_bucket": pl.String(),
            "collection_id": pl.String(),
        }
    )


class THPDecisionDatasetV1Table(_BaseTHPTable):
    _table_name = "thp_decision_dataset_v1"
    _table_schema = pl.Schema(
        {
            "decision_id": pl.String(),
            "pid": pl.Int64(),
            "tgid": pl.Int64(),
            "ts_ns": pl.Int64(),
            "candidate_type": pl.String(),
            "age_sec": pl.Float64(),
            "age_censored": pl.Boolean(),
            "window_ms": pl.Int64(),
            "miss_rate_pre": pl.Float64(),
            "walk_rate_pre": pl.Float64(),
            "delta_miss_rate": pl.Float64(),
            "delta_walk_rate": pl.Float64(),
            "delta_tlb_flush_rate": pl.Float64(),
            "utility_score": pl.Float64(),
            "label_split_preferred": pl.Boolean(),
            "label_keep_preferred": pl.Boolean(),
            "is_intervened": pl.Boolean(),
            "matched_control_decision_id": pl.String(),
            "collection_id": pl.String(),
        }
    )


def _seconds_from_ts(ts_ns: pl.Series) -> list[float]:
    if len(ts_ns) == 0:
        return []
    min_ts = int(ts_ns.min())
    return [float((ts - min_ts) / 1e9) for ts in ts_ns.to_list()]


class THPCandidateRateGraph(CollectionGraph):
    @classmethod
    def with_graph_engine(cls, graph_engine: GraphEngine) -> CollectionGraph | None:
        table = graph_engine.collection_data.get(THPCandidateTable)
        if table is None:
            return None
        return THPCandidateRateGraph(graph_engine=graph_engine, table=table)

    @classmethod
    def base_name(cls) -> str:
        return "THP Candidate Rate Over Time"

    def __init__(self, graph_engine: GraphEngine, table: THPCandidateTable):
        self.graph_engine = graph_engine
        self.table = table

    def name(self) -> str:
        return f"{self.base_name()} for Collection {self.graph_engine.collection_data.id}"

    def x_axis(self) -> str:
        return "Runtime (sec)"

    def y_axis(self) -> str:
        return "Candidates / sec"

    def plot(self) -> None:
        df = self.table.filtered_table()
        if df.is_empty():
            return
        base_ts = int(df["ts_ns"].min())
        bucket_df = df.with_columns(
            (((pl.col("ts_ns") - base_ts) / 1_000_000_000).floor().cast(pl.Int64())).alias("sec")
        ).group_by(["sec", "candidate_type"]).len()
        for candidate_type in sorted(bucket_df["candidate_type"].unique().to_list()):
            subtype = bucket_df.filter(pl.col("candidate_type") == candidate_type).sort("sec")
            self.graph_engine.plot(
                subtype["sec"].cast(pl.Float64).to_list(),
                subtype["len"].cast(pl.Float64).to_list(),
                label=str(candidate_type),
            )

    def plot_trends(self) -> None:
        pass


class THPAgeDistributionGraph(CollectionGraph):
    @classmethod
    def with_graph_engine(cls, graph_engine: GraphEngine) -> CollectionGraph | None:
        table = graph_engine.collection_data.get(THPCandidateTable)
        if table is None:
            return None
        return THPAgeDistributionGraph(graph_engine=graph_engine, table=table)

    @classmethod
    def base_name(cls) -> str:
        return "THP Age Distribution"

    def __init__(self, graph_engine: GraphEngine, table: THPCandidateTable):
        self.graph_engine = graph_engine
        self.table = table

    def name(self) -> str:
        return f"{self.base_name()} for Collection {self.graph_engine.collection_data.id}"

    def x_axis(self) -> str:
        return "Runtime (sec)"

    def y_axis(self) -> str:
        return "Age (sec)"

    def plot(self) -> None:
        df = self.table.filtered_table().filter(~pl.col("age_censored"))
        if df.is_empty():
            return
        for candidate_type in sorted(df["candidate_type"].unique().to_list()):
            subtype = df.filter(pl.col("candidate_type") == candidate_type).sort("ts_ns")
            self.graph_engine.scatter(
                _seconds_from_ts(subtype["ts_ns"]),
                subtype["age_sec"].cast(pl.Float64).to_list(),
                label=str(candidate_type),
            )

    def plot_trends(self) -> None:
        pass


class THPTranslationPressureGraph(CollectionGraph):
    @classmethod
    def with_graph_engine(cls, graph_engine: GraphEngine) -> CollectionGraph | None:
        table = graph_engine.collection_data.get(THPWindowFeatures1sTable)
        if table is None:
            return None
        return THPTranslationPressureGraph(graph_engine=graph_engine, table=table)

    @classmethod
    def base_name(cls) -> str:
        return "THP Translation Pressure"

    def __init__(self, graph_engine: GraphEngine, table: THPWindowFeatures1sTable):
        self.graph_engine = graph_engine
        self.table = table

    def name(self) -> str:
        return f"{self.base_name()} for Collection {self.graph_engine.collection_data.id}"

    def x_axis(self) -> str:
        return "Runtime (sec)"

    def y_axis(self) -> str:
        return "Rate"

    def plot(self) -> None:
        df = self.table.filtered_table().sort("ts_ns")
        if df.is_empty():
            return
        x_data = _seconds_from_ts(df["ts_ns"])
        self.graph_engine.plot(x_data, df["miss_rate_pre"].cast(pl.Float64).to_list(), label="miss_rate_pre")
        self.graph_engine.plot(x_data, df["walk_rate_pre"].cast(pl.Float64).to_list(), label="walk_rate_pre")

    def plot_trends(self) -> None:
        pass


class THPLabelBalanceGraph(CollectionGraph):
    @classmethod
    def with_graph_engine(cls, graph_engine: GraphEngine) -> CollectionGraph | None:
        table = graph_engine.collection_data.get(THPDecisionDatasetV1Table)
        if table is None:
            return None
        return THPLabelBalanceGraph(graph_engine=graph_engine, table=table)

    @classmethod
    def base_name(cls) -> str:
        return "THP Label Balance"

    def __init__(self, graph_engine: GraphEngine, table: THPDecisionDatasetV1Table):
        self.graph_engine = graph_engine
        self.table = table

    def name(self) -> str:
        return f"{self.base_name()} for Collection {self.graph_engine.collection_data.id}"

    def x_axis(self) -> str:
        return "Label"

    def y_axis(self) -> str:
        return "Count"

    def plot(self) -> None:
        df = self.table.filtered_table()
        if df.is_empty():
            return
        split_count = int(df.filter(pl.col("label_split_preferred")).height)
        keep_count = int(df.filter(pl.col("label_keep_preferred")).height)
        self.graph_engine.scatter([0.0, 1.0], [float(keep_count), float(split_count)], label="counts")

    def plot_trends(self) -> None:
        pass


THPCandidateTable._graph_types = [THPCandidateRateGraph, THPAgeDistributionGraph]
THPWindowFeatures1sTable._graph_types = [THPTranslationPressureGraph]
THPDecisionDatasetV1Table._graph_types = [THPLabelBalanceGraph]


@dataclass(frozen=True)
class THPAgeBucket:
    name: str
    min_age_sec: float
    max_age_sec: float | None


AGE_BUCKETS = (
    THPAgeBucket("young", 0.0, 30.0),
    THPAgeBucket("mid", 30.0, 300.0),
    THPAgeBucket("old", 300.0, None),
)


def age_bucket_for(age_sec: float | None, *, censored: bool) -> str:
    if censored or age_sec is None:
        return "unknown"
    for bucket in AGE_BUCKETS:
        if age_sec >= bucket.min_age_sec and (
            bucket.max_age_sec is None or age_sec < bucket.max_age_sec
        ):
            return bucket.name
    return "unknown"


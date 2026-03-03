from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import polars as pl
from data_schema.thp_harness import age_bucket_for


@dataclass(frozen=True)
class CounterIndex:
    ts_by_cpu: dict[int, list[int]]
    cumulative_by_cpu: dict[int, list[float]]

    def cumulative_at(self, ts_ns: int) -> float:
        total = 0.0
        for cpu, ts_list in self.ts_by_cpu.items():
            if not ts_list:
                continue
            idx = bisect.bisect_right(ts_list, ts_ns) - 1
            if idx < 0:
                continue
            total += self.cumulative_by_cpu[cpu][idx]
        return total


def _load_table(run_dir: Path, table_name: str) -> pl.DataFrame:
    paths = sorted(run_dir.glob(f"{table_name}.*.parquet"))
    if not paths:
        return pl.DataFrame()
    return pl.concat([pl.read_parquet(path) for path in paths], how="diagonal_relaxed")


def _redis_tgid(process_trace: pl.DataFrame) -> int | None:
    if process_trace.is_empty():
        return None
    redis_rows = process_trace.filter(pl.col("name").str.contains("redis-server"))
    if redis_rows.is_empty():
        return None
    modes = redis_rows["tgid"].mode()
    if modes.is_empty():
        return None
    return int(modes[0])


def _build_counter_index(
    table: pl.DataFrame,
    *,
    cumulative_col: str,
    tgid: int | None,
) -> CounterIndex:
    if table.is_empty() or cumulative_col not in table.columns:
        return CounterIndex(ts_by_cpu={}, cumulative_by_cpu={})
    filtered = table
    if tgid is not None and "tgid" in filtered.columns:
        filtered = filtered.filter(pl.col("tgid") == tgid)
    if filtered.is_empty():
        return CounterIndex(ts_by_cpu={}, cumulative_by_cpu={})
    if "ts_ns" not in filtered.columns and "ts_uptime_us" in filtered.columns:
        filtered = filtered.with_columns((pl.col("ts_uptime_us") * 1000).alias("ts_ns"))
    ts_by_cpu = dict[int, list[int]]()
    cumulative_by_cpu = dict[int, list[float]]()
    for cpu_key, group in filtered.group_by("cpu"):
        cpu = int(cpu_key[0])
        sorted_group = group.sort("ts_ns")
        ts_by_cpu[cpu] = sorted_group["ts_ns"].cast(pl.Int64).to_list()
        cumulative_by_cpu[cpu] = sorted_group[cumulative_col].cast(pl.Float64).to_list()
    return CounterIndex(ts_by_cpu=ts_by_cpu, cumulative_by_cpu=cumulative_by_cpu)


def _counter_delta(index: CounterIndex, start_ns: int, end_ns: int) -> float:
    if end_ns <= start_ns:
        return 0.0
    return max(index.cumulative_at(end_ns) - index.cumulative_at(start_ns), 0.0)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_parquet(run_dir: Path, name: str, df: pl.DataFrame) -> None:
    if df.is_empty():
        return
    df.write_parquet(run_dir / f"{name}.end.parquet")


def _save_empty_graph(
    path: Path,
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    message: str,
) -> None:
    plt.figure(figsize=(12, 6))
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.text(0.5, 0.5, message, ha="center", va="center", transform=plt.gca().transAxes)
    plt.grid(alpha=0.2)
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def _augment_candidates_with_scan(
    collection_id: str,
    candidates: pl.DataFrame,
    scans: pl.DataFrame,
    collapses: pl.DataFrame,
) -> pl.DataFrame:
    scan_rows = []
    if not scans.is_empty():
        collapse_by_mm = dict[str, int]()
        if not collapses.is_empty():
            collapse_sorted = collapses.sort("ts_ns")
            for row in collapse_sorted.iter_rows(named=True):
                collapse_by_mm[str(row.get("mm", ""))] = int(row["ts_ns"])
        for idx, row in enumerate(scans.iter_rows(named=True), start=1):
            ts_ns = int(row["ts_ns"])
            mm = str(row.get("mm", ""))
            collapse_ts = collapse_by_mm.get(mm)
            age_sec = (
                float((ts_ns - collapse_ts) / 1e9) if collapse_ts is not None else 0.0
            )
            age_censored = collapse_ts is None
            start_addr = int(row["pfn"]) << 21
            end_addr = start_addr + (2 * 1024 * 1024)
            scan_rows.append(
                {
                    "decision_id": f"{collection_id}-scan-{idx}",
                    "pid": int(row["pid"]),
                    "tgid": int(row["tgid"]),
                    "ts_ns": ts_ns,
                    "candidate_type": "scan",
                    "mm": mm,
                    "pfn": int(row["pfn"]),
                    "start_addr": start_addr,
                    "end_addr": end_addr,
                    "age_sec": age_sec,
                    "age_censored": age_censored,
                    "referenced": int(row.get("referenced", 0)),
                    "none_or_zero": int(row.get("none_or_zero", 0)),
                    "writable": int(row.get("writable", 0)),
                    "scan_status": int(row.get("status", 0)),
                    "anon_hugepages_kb": 0,
                    "thp_eligible": 1,
                    "recent_scan_hit": 1,
                    "collection_id": collection_id,
                }
            )
    scan_df = pl.DataFrame(scan_rows) if scan_rows else pl.DataFrame()
    if candidates.is_empty():
        merged = scan_df
    elif scan_df.is_empty():
        merged = candidates
    else:
        merged = pl.concat([candidates, scan_df], how="diagonal_relaxed")
    if merged.is_empty():
        return merged
    return (
        merged.with_columns(
            pl.col("decision_id").cast(pl.String),
            pl.col("age_censored").cast(pl.Boolean),
        )
        .unique("decision_id")
        .sort("ts_ns")
    )


def _window_features(
    candidates: pl.DataFrame,
    *,
    window_ms: int,
    instructions_idx: CounterIndex,
    dtlb_idx: CounterIndex,
    walk_idx: CounterIndex,
    tlb_flush_idx: CounterIndex,
    collection_id: str,
) -> pl.DataFrame:
    rows = []
    window_ns = int(window_ms * 1_000_000)
    for candidate in candidates.iter_rows(named=True):
        ts_ns = int(candidate["ts_ns"])
        pre_start = ts_ns - window_ns
        pre_end = ts_ns
        post_end = ts_ns + window_ns

        instr_pre = _counter_delta(instructions_idx, pre_start, pre_end)
        instr_post = _counter_delta(instructions_idx, pre_end, post_end)
        dtlb_pre = _counter_delta(dtlb_idx, pre_start, pre_end)
        dtlb_post = _counter_delta(dtlb_idx, pre_end, post_end)
        walk_pre = _counter_delta(walk_idx, pre_start, pre_end)
        walk_post = _counter_delta(walk_idx, pre_end, post_end)
        tlb_pre = _counter_delta(tlb_flush_idx, pre_start, pre_end)
        tlb_post = _counter_delta(tlb_flush_idx, pre_end, post_end)

        miss_rate_pre = dtlb_pre / max(instr_pre, 1.0)
        miss_rate_post = dtlb_post / max(instr_post, 1.0)
        walk_rate_pre = walk_pre / max(instr_pre, 1.0)
        walk_rate_post = walk_post / max(instr_post, 1.0)
        tlb_rate_pre = tlb_pre / max(instr_pre, 1.0)
        tlb_rate_post = tlb_post / max(instr_post, 1.0)

        rows.append(
            {
                "decision_id": str(candidate["decision_id"]),
                "pid": int(candidate["pid"]),
                "tgid": int(candidate["tgid"]),
                "ts_ns": ts_ns,
                "window_ms": window_ms,
                "instructions_pre": instr_pre,
                "instructions_post": instr_post,
                "dtlb_miss_pre": dtlb_pre,
                "dtlb_miss_post": dtlb_post,
                "walk_pre": walk_pre,
                "walk_post": walk_post,
                "tlb_flush_pre": tlb_pre,
                "tlb_flush_post": tlb_post,
                "miss_rate_pre": miss_rate_pre,
                "miss_rate_post": miss_rate_post,
                "walk_rate_pre": walk_rate_pre,
                "walk_rate_post": walk_rate_post,
                "tlb_flush_rate_pre": tlb_rate_pre,
                "tlb_flush_rate_post": tlb_rate_post,
                "delta_miss_rate": miss_rate_post - miss_rate_pre,
                "delta_walk_rate": walk_rate_post - walk_rate_pre,
                "delta_tlb_flush_rate": tlb_rate_post - tlb_rate_pre,
                "collection_id": collection_id,
            }
        )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def _make_decision_dataset(
    candidates: pl.DataFrame, features_1s: pl.DataFrame, collection_id: str
) -> pl.DataFrame:
    if candidates.is_empty() or features_1s.is_empty():
        return pl.DataFrame()
    merged = candidates.join(
        features_1s.select(
            [
                "decision_id",
                "window_ms",
                "miss_rate_pre",
                "walk_rate_pre",
                "delta_miss_rate",
                "delta_walk_rate",
                "delta_tlb_flush_rate",
            ]
        ),
        on="decision_id",
        how="inner",
    )
    if merged.is_empty():
        return merged
    merged = merged.with_columns(
        (
            -(pl.col("delta_miss_rate") + pl.col("delta_walk_rate"))
            - (0.1 * pl.col("delta_tlb_flush_rate"))
        ).alias("utility_score")
    )
    merged = merged.with_columns(
        (pl.col("utility_score") < 0).alias("label_split_preferred"),
        (pl.col("utility_score") >= 0).alias("label_keep_preferred"),
        pl.lit(False).alias("is_intervened"),
        pl.lit("").alias("matched_control_decision_id"),
    )
    return merged.select(
        [
            "decision_id",
            "pid",
            "tgid",
            "ts_ns",
            "candidate_type",
            "age_sec",
            "age_censored",
            "window_ms",
            "miss_rate_pre",
            "walk_rate_pre",
            "delta_miss_rate",
            "delta_walk_rate",
            "delta_tlb_flush_rate",
            "utility_score",
            "label_split_preferred",
            "label_keep_preferred",
            "is_intervened",
            "matched_control_decision_id",
            "collection_id",
        ]
    ).with_columns(pl.lit(collection_id).alias("collection_id"))


def _match_interventions(
    decisions: pl.DataFrame, interventions: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    if decisions.is_empty() or interventions.is_empty():
        return pl.DataFrame(), decisions
    successful = interventions.filter(pl.col("success"))
    if successful.is_empty():
        return pl.DataFrame(), decisions

    decision_rows = decisions.to_dicts()
    by_id = {str(row["decision_id"]): row for row in decision_rows}
    matched_rows = []
    updated = decisions.clone()
    for iv in successful.iter_rows(named=True):
        treated_id = str(iv["decision_id"])
        treated = by_id.get(treated_id)
        if treated is None:
            continue
        treated_age_bucket = age_bucket_for(
            float(treated.get("age_sec", 0.0)),
            censored=bool(treated.get("age_censored", True)),
        )
        treated_ts = int(treated["ts_ns"])
        candidates = []
        for row in decision_rows:
            row_id = str(row["decision_id"])
            if row_id == treated_id:
                continue
            age_bucket = age_bucket_for(
                float(row.get("age_sec", 0.0)),
                censored=bool(row.get("age_censored", True)),
            )
            if age_bucket != treated_age_bucket:
                continue
            if abs(int(row["ts_ns"]) - treated_ts) > int(60 * 1e9):
                continue
            dist = math.sqrt(
                (float(row["miss_rate_pre"]) - float(treated["miss_rate_pre"])) ** 2
                + (float(row["walk_rate_pre"]) - float(treated["walk_rate_pre"])) ** 2
                + (float(row["delta_miss_rate"]) - float(treated["delta_miss_rate"]))
                ** 2
                + (float(row["delta_walk_rate"]) - float(treated["delta_walk_rate"]))
                ** 2
            )
            candidates.append((dist, row))
        if not candidates:
            continue
        candidates.sort(key=lambda x: x[0])
        best_dist, control = candidates[0]
        control_id = str(control["decision_id"])
        treated_delta = float(treated["delta_miss_rate"]) + float(
            treated["delta_walk_rate"]
        )
        control_delta = float(control["delta_miss_rate"]) + float(
            control["delta_walk_rate"]
        )
        split_preferred = treated_delta < control_delta
        matched_rows.append(
            {
                "intervention_id": str(iv["intervention_id"]),
                "decision_id_treated": treated_id,
                "decision_id_control": control_id,
                "distance": float(best_dist),
                "age_bucket": treated_age_bucket,
                "collection_id": str(iv["collection_id"]),
            }
        )
        updated = updated.with_columns(
            pl.when(pl.col("decision_id") == treated_id)
            .then(pl.lit(True))
            .otherwise(pl.col("is_intervened"))
            .alias("is_intervened"),
            pl.when(pl.col("decision_id") == treated_id)
            .then(pl.lit(control_id))
            .otherwise(pl.col("matched_control_decision_id"))
            .alias("matched_control_decision_id"),
            pl.when(pl.col("decision_id") == treated_id)
            .then(pl.lit(split_preferred))
            .otherwise(pl.col("label_split_preferred"))
            .alias("label_split_preferred"),
            pl.when(pl.col("decision_id") == treated_id)
            .then(pl.lit(not split_preferred))
            .otherwise(pl.col("label_keep_preferred"))
            .alias("label_keep_preferred"),
        )
    return pl.DataFrame(matched_rows), updated


def _graph_candidate_rate(graphs_dir: Path, candidates: pl.DataFrame) -> None:
    if candidates.is_empty():
        _save_empty_graph(
            graphs_dir / "thp_candidate_rate_over_time.png",
            title="THP Candidate Rate Over Time",
            xlabel="Runtime (sec)",
            ylabel="Candidates/sec",
            message="No candidate rows available.",
        )
        return
    base_ts = int(candidates["ts_ns"].min())
    grouped = (
        candidates.with_columns(
            (
                ((pl.col("ts_ns") - base_ts) / 1_000_000_000).floor().cast(pl.Int64())
            ).alias("sec")
        )
        .group_by(["sec", "candidate_type"])
        .len()
    )
    plt.figure(figsize=(12, 6))
    for candidate_type in sorted(grouped["candidate_type"].unique().to_list()):
        subset = grouped.filter(pl.col("candidate_type") == candidate_type).sort("sec")
        plt.plot(
            subset["sec"].to_list(),
            subset["len"].to_list(),
            label=str(candidate_type),
        )
    plt.xlabel("Runtime (sec)")
    plt.ylabel("Candidates/sec")
    plt.title("THP Candidate Rate Over Time")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(graphs_dir / "thp_candidate_rate_over_time.png")
    plt.close()


def _graph_age_distribution(graphs_dir: Path, candidates: pl.DataFrame) -> None:
    df = candidates.filter(~pl.col("age_censored"))
    if df.is_empty():
        _save_empty_graph(
            graphs_dir / "thp_age_distribution.png",
            title="THP Age Distribution",
            xlabel="Age (sec)",
            ylabel="Count",
            message="No uncensored age samples available.",
        )
        return
    plt.figure(figsize=(12, 6))
    for candidate_type in sorted(df["candidate_type"].unique().to_list()):
        subset = df.filter(pl.col("candidate_type") == candidate_type)
        plt.hist(
            subset["age_sec"].to_list(),
            bins=25,
            alpha=0.45,
            label=str(candidate_type),
        )
    plt.xlabel("Age (sec)")
    plt.ylabel("Count")
    plt.title("THP Age Distribution")
    plt.legend()
    plt.grid(alpha=0.2)
    plt.tight_layout()
    plt.savefig(graphs_dir / "thp_age_distribution.png")
    plt.close()


def _graph_translation_pressure(graphs_dir: Path, features_1s: pl.DataFrame) -> None:
    if features_1s.is_empty():
        _save_empty_graph(
            graphs_dir / "translation_pressure_vs_time.png",
            title="Translation Pressure vs Time",
            xlabel="Runtime (sec)",
            ylabel="Rate",
            message="No 1s window feature rows available.",
        )
        return
    base_ts = int(features_1s["ts_ns"].min())
    df = features_1s.with_columns(
        (((pl.col("ts_ns") - base_ts) / 1_000_000_000).cast(pl.Float64())).alias("sec")
    ).sort("sec")
    plt.figure(figsize=(12, 6))
    plt.plot(df["sec"].to_list(), df["miss_rate_pre"].to_list(), label="miss_rate_pre")
    plt.plot(df["sec"].to_list(), df["walk_rate_pre"].to_list(), label="walk_rate_pre")
    plt.xlabel("Runtime (sec)")
    plt.ylabel("Rate")
    plt.title("Translation Pressure vs Time")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(graphs_dir / "translation_pressure_vs_time.png")
    plt.close()


def _graph_compaction_pressure(
    graphs_dir: Path, compaction: pl.DataFrame, migrate: pl.DataFrame
) -> None:
    if compaction.is_empty() and migrate.is_empty():
        _save_empty_graph(
            graphs_dir / "compaction_pressure_vs_time.png",
            title="Compaction Pressure vs Time",
            xlabel="Runtime (sec)",
            ylabel="Count",
            message="No compaction or migrate events available.",
        )
        return
    plt.figure(figsize=(12, 6))
    if not compaction.is_empty():
        base_ts = int(compaction["ts_ns"].min())
        cdf = (
            compaction.with_columns(
                (
                    ((pl.col("ts_ns") - base_ts) / 1_000_000_000)
                    .floor()
                    .cast(pl.Int64())
                ).alias("sec")
            )
            .group_by("sec")
            .len()
            .sort("sec")
        )
        plt.plot(cdf["sec"].to_list(), cdf["len"].to_list(), label="compaction_events")
    if not migrate.is_empty():
        base_ts = int(migrate["ts_ns"].min())
        mdf = (
            migrate.with_columns(
                (
                    ((pl.col("ts_ns") - base_ts) / 1_000_000_000)
                    .floor()
                    .cast(pl.Int64())
                ).alias("sec")
            )
            .group_by("sec")
            .agg(pl.col("thp_split").sum().alias("thp_split"))
            .sort("sec")
        )
        plt.plot(
            mdf["sec"].to_list(), mdf["thp_split"].to_list(), label="migrate_thp_split"
        )
    plt.xlabel("Runtime (sec)")
    plt.ylabel("Count")
    plt.title("Compaction Pressure vs Time")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(graphs_dir / "compaction_pressure_vs_time.png")
    plt.close()


def _graph_intervention_timeline(
    graphs_dir: Path,
    interventions: pl.DataFrame,
    matches: pl.DataFrame,
    decisions: pl.DataFrame,
) -> None:
    if interventions.is_empty():
        _save_empty_graph(
            graphs_dir / "intervention_timeline.png",
            title="Intervention Timeline",
            xlabel="Runtime (sec)",
            ylabel="Event",
            message="No intervention rows available.",
        )
        return
    decision_ts = dict(decisions.select(["decision_id", "ts_ns"]).iter_rows())
    base_ts = int(interventions["ts_ns"].min())
    plt.figure(figsize=(12, 6))
    treated_x = [
        (int(row["ts_ns"]) - base_ts) / 1e9
        for row in interventions.iter_rows(named=True)
    ]
    plt.scatter(treated_x, [1.0] * len(treated_x), label="intervened", alpha=0.8)
    if not matches.is_empty():
        control_x = []
        for row in matches.iter_rows(named=True):
            control_ts = decision_ts.get(str(row["decision_id_control"]))
            if control_ts is not None:
                control_x.append((int(control_ts) - base_ts) / 1e9)
        if control_x:
            plt.scatter(
                control_x, [0.0] * len(control_x), label="matched_control", alpha=0.8
            )
    plt.yticks([0, 1], ["control", "intervened"])
    plt.xlabel("Runtime (sec)")
    plt.ylabel("Event")
    plt.title("Intervention Timeline")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(graphs_dir / "intervention_timeline.png")
    plt.close()


def _graph_split_uplift(
    graphs_dir: Path, matches: pl.DataFrame, decisions: pl.DataFrame
) -> None:
    if matches.is_empty():
        _save_empty_graph(
            graphs_dir / "split_uplift_vs_control.png",
            title="Split Uplift vs Control",
            xlabel="Matched Pair Index",
            ylabel="Delta Uplift (treated-control)",
            message="No matched intervention-control pairs available.",
        )
        return
    decision_map = {
        str(row["decision_id"]): row for row in decisions.iter_rows(named=True)
    }
    x = []
    y = []
    for idx, row in enumerate(matches.iter_rows(named=True), start=1):
        treated = decision_map.get(str(row["decision_id_treated"]))
        control = decision_map.get(str(row["decision_id_control"]))
        if not treated or not control:
            continue
        treated_delta = float(treated["delta_miss_rate"]) + float(
            treated["delta_walk_rate"]
        )
        control_delta = float(control["delta_miss_rate"]) + float(
            control["delta_walk_rate"]
        )
        uplift = treated_delta - control_delta
        x.append(idx)
        y.append(uplift)
    if not x:
        _save_empty_graph(
            graphs_dir / "split_uplift_vs_control.png",
            title="Split Uplift vs Control",
            xlabel="Matched Pair Index",
            ylabel="Delta Uplift (treated-control)",
            message="No matched intervention-control pairs available.",
        )
        return
    plt.figure(figsize=(12, 6))
    plt.scatter(x, y, alpha=0.8)
    plt.axhline(0.0, color="black", linestyle="--")
    plt.xlabel("Matched Pair Index")
    plt.ylabel("Delta Uplift (treated-control)")
    plt.title("Split Uplift vs Control")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(graphs_dir / "split_uplift_vs_control.png")
    plt.close()


def _graph_label_balance(graphs_dir: Path, decisions: pl.DataFrame) -> None:
    if decisions.is_empty():
        _save_empty_graph(
            graphs_dir / "label_balance.png",
            title="Label Balance",
            xlabel="Label",
            ylabel="Count",
            message="No decision rows available.",
        )
        return
    keep_count = int(decisions.filter(pl.col("label_keep_preferred")).height)
    split_count = int(decisions.filter(pl.col("label_split_preferred")).height)
    plt.figure(figsize=(8, 5))
    plt.bar(["keep", "split"], [keep_count, split_count], color=["#5B8FF9", "#F08A5D"])
    plt.ylabel("Count")
    plt.title("Label Balance")
    plt.tight_layout()
    plt.savefig(graphs_dir / "label_balance.png")
    plt.close()


def _graph_quality_waterfall(
    graphs_dir: Path,
    *,
    candidate_count: int,
    features_count: int,
    decision_count: int,
    intervention_count: int,
    match_count: int,
) -> None:
    stages = [
        "raw_candidates",
        "windowed_rows",
        "decision_rows",
        "interventions",
        "matches",
    ]
    counts = [
        candidate_count,
        features_count,
        decision_count,
        intervention_count,
        match_count,
    ]
    plt.figure(figsize=(10, 5))
    plt.bar(stages, counts, color="#4C956C")
    plt.ylabel("Rows")
    plt.title("Data Quality Waterfall")
    plt.xticks(rotation=20)
    plt.tight_layout()
    plt.savefig(graphs_dir / "data_quality_waterfall.png")
    plt.close()


def _generate_run_graphs(
    run_dir: Path,
    *,
    candidates: pl.DataFrame,
    features_1s: pl.DataFrame,
    compaction: pl.DataFrame,
    migrate: pl.DataFrame,
    interventions: pl.DataFrame,
    matches: pl.DataFrame,
    decisions: pl.DataFrame,
) -> None:
    graphs_dir = run_dir / "thp_graphs"
    _ensure_dir(graphs_dir)
    _graph_candidate_rate(graphs_dir, candidates)
    _graph_age_distribution(graphs_dir, candidates)
    _graph_translation_pressure(graphs_dir, features_1s)
    _graph_compaction_pressure(graphs_dir, compaction, migrate)
    _graph_intervention_timeline(graphs_dir, interventions, matches, decisions)
    _graph_split_uplift(graphs_dir, matches, decisions)
    _graph_label_balance(graphs_dir, decisions)
    _graph_quality_waterfall(
        graphs_dir,
        candidate_count=candidates.height,
        features_count=features_1s.height,
        decision_count=decisions.height,
        intervention_count=interventions.height,
        match_count=matches.height,
    )


def process_thp_harness_run(
    run_dir: Path,
    *,
    window_ms: list[int],
    matching_enabled: bool,
    output_graphs: bool,
) -> None:
    system_info = _load_table(run_dir, "system_info")
    if system_info.is_empty():
        return
    collection_id = (
        str(system_info[0, "collection_id"])
        if "collection_id" in system_info.columns
        else run_dir.name
    )

    process_trace = _load_table(run_dir, "process_trace")
    candidates = _load_table(run_dir, "thp_candidates")
    scans = _load_table(run_dir, "thp_scan_events")
    collapses = _load_table(run_dir, "thp_collapse_events")
    compaction = _load_table(run_dir, "thp_compaction_events")
    migrate = _load_table(run_dir, "thp_migrate_events")
    interventions = _load_table(run_dir, "thp_interventions")

    candidates = _augment_candidates_with_scan(
        collection_id, candidates, scans, collapses
    )
    _write_parquet(run_dir, "thp_candidates", candidates)
    if candidates.is_empty():
        return

    redis_tgid = _redis_tgid(process_trace)
    instructions = _load_table(run_dir, "instructions_retired")
    dtlb_miss = _load_table(run_dir, "dtlb_misses")
    walk = _load_table(run_dir, "dtlb_walk_duration")
    tlb_flush = _load_table(run_dir, "tlb_flushes")

    instructions_idx = _build_counter_index(
        instructions, cumulative_col="cumulative_instructions_retired", tgid=redis_tgid
    )
    dtlb_idx = _build_counter_index(
        dtlb_miss, cumulative_col="cumulative_dtlb_misses", tgid=redis_tgid
    )
    walk_idx = _build_counter_index(
        walk, cumulative_col="cumulative_dtlb_walk_duration", tgid=redis_tgid
    )
    tlb_idx = _build_counter_index(
        tlb_flush, cumulative_col="cumulative_tlb_flushes", tgid=redis_tgid
    )

    features_by_window = {}
    for window in window_ms:
        features = _window_features(
            candidates,
            window_ms=int(window),
            instructions_idx=instructions_idx,
            dtlb_idx=dtlb_idx,
            walk_idx=walk_idx,
            tlb_flush_idx=tlb_idx,
            collection_id=collection_id,
        )
        features_by_window[int(window)] = features
        if int(window) == 100:
            _write_parquet(run_dir, "thp_window_features_100ms", features)
        elif int(window) == 1000:
            _write_parquet(run_dir, "thp_window_features_1s", features)
        elif int(window) == 10000:
            _write_parquet(run_dir, "thp_window_features_10s", features)
        else:
            _write_parquet(run_dir, f"thp_window_features_{int(window)}ms", features)

    features_1s = features_by_window.get(1000, pl.DataFrame())
    decisions = _make_decision_dataset(candidates, features_1s, collection_id)
    matches = pl.DataFrame()
    if matching_enabled and not decisions.is_empty() and not interventions.is_empty():
        matches, decisions = _match_interventions(decisions, interventions)

    _write_parquet(run_dir, "thp_intervention_match", matches)
    _write_parquet(run_dir, "thp_decision_dataset_v1", decisions)

    if output_graphs:
        _generate_run_graphs(
            run_dir,
            candidates=candidates,
            features_1s=features_1s,
            compaction=compaction,
            migrate=migrate,
            interventions=interventions,
            matches=matches,
            decisions=decisions,
        )


def generate_cross_run_graphs(base_dir: Path, *, benchmark: str = "redis") -> None:
    benchmark_dir = base_dir / benchmark
    if not benchmark_dir.exists():
        return
    decision_rows = []
    runtime_rows = []
    for run_dir in sorted(benchmark_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        decisions = _load_table(run_dir, "thp_decision_dataset_v1")
        system_info = _load_table(run_dir, "system_info")
        if not system_info.is_empty():
            collection_id = (
                str(system_info[0, "collection_id"])
                if "collection_id" in system_info.columns
                else run_dir.name
            )
            thp_mode = (
                str(system_info[0, "transparent_hugepages"])
                if "transparent_hugepages" in system_info.columns
                else "unknown"
            )
            runtime_s = (
                float(system_info[0, "collection_time_sec"])
                if "collection_time_sec" in system_info.columns
                else 0.0
            )
            runtime_rows.append(
                {
                    "collection_id": collection_id,
                    "thp_mode": thp_mode,
                    "runtime_s": runtime_s,
                }
            )
        if decisions.is_empty():
            continue
        thp_mode = "unknown"
        if not system_info.is_empty():
            if "transparent_hugepages" in system_info.columns:
                thp_mode = str(system_info[0, "transparent_hugepages"])
        decision_rows.extend(
            [
                {
                    **row,
                    "thp_mode": thp_mode,
                }
                for row in decisions.to_dicts()
            ]
        )
    if not decision_rows:
        return
    out_dir = benchmark_dir / "thp_summary_graphs"
    _ensure_dir(out_dir)
    decisions_df = pl.DataFrame(decision_rows)

    plt.figure(figsize=(10, 6))
    modes = sorted(decisions_df["thp_mode"].unique().to_list())
    values = [
        decisions_df.filter(pl.col("thp_mode") == mode)["utility_score"].to_list()
        for mode in modes
    ]
    plt.boxplot(values, labels=modes)
    plt.ylabel("Utility Score")
    plt.title("Run Comparison Utility by THP Mode")
    plt.tight_layout()
    plt.savefig(out_dir / "run_comparison_utility_boxplot.png")
    plt.close()

    if runtime_rows:
        runtime_df = pl.DataFrame(runtime_rows)
        baseline = runtime_df.filter(pl.col("thp_mode") == "never")
        baseline_runtime = (
            float(baseline["runtime_s"].mean()) if not baseline.is_empty() else None
        )
        chart_modes = sorted(runtime_df["thp_mode"].unique().to_list())
        overheads = []
        for mode in chart_modes:
            mode_runtime = float(
                runtime_df.filter(pl.col("thp_mode") == mode)["runtime_s"].mean()
            )
            if baseline_runtime is None or baseline_runtime <= 0:
                overheads.append(0.0)
            else:
                overheads.append(
                    ((mode_runtime - baseline_runtime) / baseline_runtime) * 100.0
                )
        plt.figure(figsize=(9, 5))
        plt.bar(chart_modes, overheads, color="#2A9D8F")
        plt.ylabel("Runtime Overhead vs never (%)")
        plt.title("Overhead vs Baseline")
        plt.tight_layout()
        plt.savefig(out_dir / "overhead_vs_baseline.png")
        plt.close()

    numeric_features = [
        "age_sec",
        "miss_rate_pre",
        "walk_rate_pre",
        "delta_miss_rate",
        "delta_walk_rate",
        "delta_tlb_flush_rate",
        "utility_score",
    ]
    feat_rows = []
    labeled = decisions_df.with_columns(
        pl.col("label_split_preferred").cast(pl.Int64).alias("label_int")
    )
    for feature in numeric_features:
        if feature not in labeled.columns:
            continue
        subset = labeled.select([feature, "label_int"]).drop_nulls()
        if subset.is_empty():
            continue
        corr = (
            subset.select(pl.corr(feature, "label_int")).item()
            if subset.height > 1
            else 0.0
        )
        corr = 0.0 if corr is None or math.isnan(float(corr)) else abs(float(corr))
        feat_rows.append({"feature": feature, "importance": corr})
    if feat_rows:
        feat_df = pl.DataFrame(feat_rows).sort("importance", descending=True)
        plt.figure(figsize=(10, 6))
        plt.barh(
            feat_df["feature"].to_list()[::-1],
            feat_df["importance"].to_list()[::-1],
            color="#E76F51",
        )
        plt.xlabel("Absolute Correlation with label_split_preferred")
        plt.title("Feature Importance Preview")
        plt.tight_layout()
        plt.savefig(out_dir / "feature_importance_preview.png")
        plt.close()

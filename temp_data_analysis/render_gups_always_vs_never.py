#!/usr/bin/env python3
"""Render always-vs-never GUPS sanity-check charts and dashboard."""

from __future__ import annotations

import argparse
import bisect
import json
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import polars as pl


REPO_ROOT = Path("/users/SahilSC/HugePageResearch")
DATA_ROOT = REPO_ROOT / "data" / "curated" / "gups"
OUTPUT_ROOT = REPO_ROOT / "temp_data_analysis"
DEFAULT_ALWAYS_CONFIG = REPO_ROOT / "config" / "gups_always_sanity.yaml"
DEFAULT_NEVER_CONFIG = REPO_ROOT / "config" / "gups_never_sanity.yaml"

COLLECTION_ID_RE = re.compile(r"^Collection_id:\s+(\S+)$", re.MULTILINE)

ALWAYS_COLOR = "#2a9d8f"
NEVER_COLOR = "#e76f51"
FIGSIZE = (12.8, 7.68)
GIB_DIVISOR = float(1024 * 1024)


@dataclass(frozen=True)
class RepeatResult:
    """One GUPS repeat emitted by ``gups_results.jsonl``."""

    repeat_index: int
    start_boottime_ns: int
    end_boottime_ns: int
    runtime_s: float
    gups: float
    table_bytes: int
    updates: int
    verification_errors: int
    verification_passed: bool
    threads: int


@dataclass(frozen=True)
class RollupPoint:
    """One process-local THP sample from ``smaps_rollup_samples``."""

    ts_ns: int
    anon_hugepages_kb: int


@dataclass(frozen=True)
class CollectionSeries:
    """Graphable data for one GUPS THP mode."""

    mode_label: str
    color: str
    collection_id: str
    thp_mode: str
    run_dir: Path
    repeats: list[RepeatResult]
    anon_hugepages_gib: list[float]
    dtlb_misses: list[int]
    dtlb_loads: list[int]
    dtlb_misses_scope: str
    dtlb_loads_scope: str


def parse_args() -> argparse.Namespace:
    """Return CLI arguments for the GUPS always-vs-never renderer."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--always-stdout", type=Path, required=True)
    parser.add_argument("--never-stdout", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--always-config", type=Path, default=DEFAULT_ALWAYS_CONFIG)
    parser.add_argument("--never-config", type=Path, default=DEFAULT_NEVER_CONFIG)
    return parser.parse_args()


def parse_collection_id(stdout_log_path: Path) -> str:
    """Extract the printed collection id from one preserved ``collect`` log."""

    raw = stdout_log_path.read_text(encoding="utf-8")
    match = COLLECTION_ID_RE.search(raw)
    if match is None:
        raise RuntimeError(f"Missing Collection_id in {stdout_log_path}")
    return match.group(1)


def chunk_sort_key(file_path: Path) -> tuple[int, int | str]:
    """Return a stable sort key for chunked parquet filenames."""

    parts = file_path.name.split(".")
    if len(parts) >= 3:
        chunk = parts[-2]
        if chunk == "end":
            return (1, 0)
        if chunk.isdigit():
            return (0, int(chunk))
    return (0, file_path.name)


def load_table(run_dir: Path, table_name: str) -> pl.DataFrame:
    """Load one chunked parquet table from a curated run directory."""

    parquet_files = sorted(run_dir.glob(f"{table_name}.*.parquet"), key=chunk_sort_key)
    if not parquet_files:
        raise RuntimeError(f"Missing {table_name} parquet files in {run_dir}")
    return pl.concat(
        [pl.read_parquet(file_path) for file_path in parquet_files],
        how="diagonal_relaxed",
    )


def load_repeat_results(run_dir: Path) -> list[RepeatResult]:
    """Parse one run directory's ``gups_results.jsonl`` output."""

    results_path = run_dir / "gups_results.jsonl"
    if not results_path.is_file():
        raise RuntimeError(f"Missing gups_results.jsonl in {run_dir}")

    repeats = []
    for raw_line in results_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        row = json.loads(raw_line)
        repeats.append(
            RepeatResult(
                repeat_index=int(row["repeat_index"]),
                start_boottime_ns=int(row["start_boottime_ns"]),
                end_boottime_ns=int(row["end_boottime_ns"]),
                runtime_s=float(row["runtime_s"]),
                gups=float(row["gups"]),
                table_bytes=int(row["table_bytes"]),
                updates=int(row["updates"]),
                verification_errors=int(row.get("verification_errors", 0)),
                verification_passed=bool(row["verification_passed"]),
                threads=int(row.get("threads", 0)),
            )
        )
    if not repeats:
        raise RuntimeError(f"{run_dir} has no repeat rows in gups_results.jsonl")
    return sorted(repeats, key=lambda row: row.repeat_index)


def discover_gups_tgid(process_trace_df: pl.DataFrame) -> int:
    """Return the unique GUPS TGID from ``process_trace``."""

    required = {"name", "tgid"}
    if not required.issubset(set(process_trace_df.columns)):
        raise RuntimeError("process_trace is missing name/tgid columns for GUPS discovery")

    rows = process_trace_df.filter(pl.col("name").str.starts_with("gups"))
    if rows.is_empty():
        raise RuntimeError("process_trace has no rows whose name starts with 'gups'")

    tgids = sorted(set(rows["tgid"].to_list()))
    if len(tgids) != 1:
        raise RuntimeError(f"Expected one gups TGID, found {tgids}")
    return int(tgids[0])


def load_rollup_points(run_dir: Path, gups_tgid: int) -> list[RollupPoint]:
    """Return process-local rollup samples for one GUPS collection."""

    rollup_df = load_table(run_dir, "smaps_rollup_samples")
    if "tgid" not in rollup_df.columns:
        raise RuntimeError(f"{run_dir} smaps_rollup_samples is missing tgid")
    filtered = rollup_df.filter(pl.col("tgid") == gups_tgid).sort("ts_ns")
    if filtered.is_empty():
        raise RuntimeError(f"{run_dir} has no smaps_rollup_samples rows for TGID {gups_tgid}")

    return [
        RollupPoint(
            ts_ns=int(row["ts_ns"]),
            anon_hugepages_kb=int(row["anon_hugepages_kb"]),
        )
        for row in filtered.to_dicts()
    ]


def latest_rollup_at_or_before(points: list[RollupPoint], target_ns: int) -> RollupPoint:
    """Return the latest rollup sample at or before one repeat-end timestamp."""

    ts_values = [point.ts_ns for point in points]
    index = bisect.bisect_right(ts_values, target_ns) - 1
    if index < 0:
        raise RuntimeError(f"No rollup sample exists at or before ts_ns={target_ns}")
    return points[index]


def _latest_perf_count_at_or_before(
    cpu_rows: list[dict[str, int]],
    target_us: int,
    counter_column: str,
) -> int | None:
    ts_values = [int(row["ts_uptime_us"]) for row in cpu_rows]
    index = bisect.bisect_right(ts_values, target_us) - 1
    if index < 0:
        return None
    return int(cpu_rows[index][counter_column])


def find_perf_counter_column(perf_df: pl.DataFrame, table_name: str) -> str:
    """Return the cumulative counter column emitted for one perf table."""

    if "cumulative_count" in perf_df.columns:
        return "cumulative_count"

    cumulative_columns = [
        column_name for column_name in perf_df.columns if column_name.startswith("cumulative_")
    ]
    if len(cumulative_columns) == 1:
        return cumulative_columns[0]
    raise RuntimeError(
        f"{table_name} in the collection must contain one cumulative counter column; "
        f"found {cumulative_columns}"
    )


def load_perf_repeat_deltas(
    run_dir: Path,
    table_name: str,
    gups_tgid: int,
    repeats: list[RepeatResult],
) -> tuple[list[int], str]:
    """Return one cumulative perf delta per repeat for the requested table."""

    perf_df = load_table(run_dir, table_name)
    required = {"tgid", "cpu", "ts_uptime_us"}
    if not required.issubset(set(perf_df.columns)):
        raise RuntimeError(f"{table_name} in {run_dir} is missing required perf columns")

    counter_column = find_perf_counter_column(perf_df, table_name)
    process_filtered = perf_df.filter(pl.col("tgid") == gups_tgid).sort(["cpu", "ts_uptime_us"])
    if not process_filtered.is_empty():
        filtered = process_filtered
        perf_scope = "process-local"
    else:
        filtered = perf_df.filter(pl.col("tgid") == 0).sort(["cpu", "ts_uptime_us"])
        if filtered.is_empty():
            raise RuntimeError(
                f"{run_dir} has no {table_name} rows for TGID {gups_tgid} or fallback tgid=0"
            )
        perf_scope = "system-wide"

    cpu_rows: dict[int, list[dict[str, int]]] = {}
    for cpu_key, group in filtered.group_by("cpu", maintain_order=True):
        cpu_value = cpu_key[0] if isinstance(cpu_key, tuple) else cpu_key
        cpu_rows[int(cpu_value)] = group.sort("ts_uptime_us").to_dicts()

    deltas: list[int] = []
    for repeat in repeats:
        start_us = repeat.start_boottime_ns // 1000
        end_us = repeat.end_boottime_ns // 1000
        total = 0
        for rows in cpu_rows.values():
            end_count = _latest_perf_count_at_or_before(rows, end_us, counter_column)
            if end_count is None:
                continue
            start_count = _latest_perf_count_at_or_before(rows, start_us, counter_column)
            total += max(0, end_count - (start_count or 0))
        deltas.append(total)
    return deltas, perf_scope


def load_collection_series(
    *,
    mode_label: str,
    color: str,
    stdout_log_path: Path,
    data_root: Path,
) -> CollectionSeries:
    """Load one GUPS collection and align rollup/perf data to repeat ends."""

    collection_id = parse_collection_id(stdout_log_path)
    run_dir = data_root / collection_id
    if not run_dir.is_dir():
        raise RuntimeError(f"Missing curated run directory {run_dir}")

    repeats = load_repeat_results(run_dir)
    system_info = load_table(run_dir, "system_info")
    process_trace = load_table(run_dir, "process_trace")
    gups_tgid = discover_gups_tgid(process_trace)
    thp_mode = (
        str(system_info[0, "transparent_hugepages"])
        if "transparent_hugepages" in system_info.columns
        else "unknown"
    )

    rollup_points = load_rollup_points(run_dir, gups_tgid)
    anon_hugepages_gib = [
        latest_rollup_at_or_before(rollup_points, repeat.end_boottime_ns).anon_hugepages_kb
        / GIB_DIVISOR
        for repeat in repeats
    ]
    dtlb_misses, dtlb_misses_scope = load_perf_repeat_deltas(
        run_dir,
        "dtlb_misses",
        gups_tgid,
        repeats,
    )
    dtlb_loads, dtlb_loads_scope = load_perf_repeat_deltas(
        run_dir,
        "dtlb_loads",
        gups_tgid,
        repeats,
    )

    return CollectionSeries(
        mode_label=mode_label,
        color=color,
        collection_id=collection_id,
        thp_mode=thp_mode,
        run_dir=run_dir,
        repeats=repeats,
        anon_hugepages_gib=anon_hugepages_gib,
        dtlb_misses=dtlb_misses,
        dtlb_loads=dtlb_loads,
        dtlb_misses_scope=dtlb_misses_scope,
        dtlb_loads_scope=dtlb_loads_scope,
    )


def plot_metric(
    *,
    output_path: Path,
    title: str,
    ylabel: str,
    x_values: list[int],
    always_values: list[float] | list[int],
    never_values: list[float] | list[int],
) -> None:
    """Write one always-vs-never repeat chart."""

    plt.figure(figsize=FIGSIZE)
    plt.plot(
        x_values,
        always_values,
        color=ALWAYS_COLOR,
        marker="o",
        linewidth=2.5,
        label="THP always",
    )
    plt.plot(
        x_values,
        never_values,
        color=NEVER_COLOR,
        marker="o",
        linewidth=2.5,
        label="THP never",
    )
    plt.xlabel("Repeat")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.grid(alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def build_table_rows(always: CollectionSeries, never: CollectionSeries) -> list[dict[str, object]]:
    """Return table rows used by the dashboard and config markdown."""

    rows = []
    for index, (always_repeat, never_repeat) in enumerate(zip(always.repeats, never.repeats), start=1):
        rows.append(
            {
                "repeat": index,
                "always_gups": always_repeat.gups,
                "never_gups": never_repeat.gups,
                "always_runtime_s": always_repeat.runtime_s,
                "never_runtime_s": never_repeat.runtime_s,
                "always_anon_hugepages_gib": always.anon_hugepages_gib[index - 1],
                "never_anon_hugepages_gib": never.anon_hugepages_gib[index - 1],
                "always_dtlb_misses": always.dtlb_misses[index - 1],
                "never_dtlb_misses": never.dtlb_misses[index - 1],
                "always_dtlb_loads": always.dtlb_loads[index - 1],
                "never_dtlb_loads": never.dtlb_loads[index - 1],
                "always_verification_passed": always_repeat.verification_passed,
                "never_verification_passed": never_repeat.verification_passed,
            }
        )
    return rows


def write_dashboard(
    *,
    output_dir: Path,
    label: str,
    always: CollectionSeries,
    never: CollectionSeries,
    rows: list[dict[str, object]],
) -> Path:
    """Write the standalone GUPS sanity-check HTML dashboard."""

    html_path = output_dir / f"gups_always_vs_never_{label}.html"
    row_html = "\n".join(
        [
            "<tr>"
            f"<td>{int(row['repeat'])}</td>"
            f"<td>{float(row['always_gups']):.6f}</td>"
            f"<td>{float(row['never_gups']):.6f}</td>"
            f"<td>{float(row['always_runtime_s']):.6f}</td>"
            f"<td>{float(row['never_runtime_s']):.6f}</td>"
            f"<td>{float(row['always_anon_hugepages_gib']):.3f}</td>"
            f"<td>{float(row['never_anon_hugepages_gib']):.3f}</td>"
            f"<td>{int(row['always_dtlb_misses'])}</td>"
            f"<td>{int(row['never_dtlb_misses'])}</td>"
            f"<td>{int(row['always_dtlb_loads'])}</td>"
            f"<td>{int(row['never_dtlb_loads'])}</td>"
            f"<td>{escape(str(row['always_verification_passed']).lower())}</td>"
            f"<td>{escape(str(row['never_verification_passed']).lower())}</td>"
            "</tr>"
            for row in rows
        ]
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>GUPS Always-vs-Never Sanity Check</title>
  <style>
    body {{
      font-family: "Iosevka Web", "IBM Plex Sans", sans-serif;
      margin: 0;
      padding: 32px;
      background: linear-gradient(180deg, #f6f2e9 0%, #f2efe8 100%);
      color: #1f2933;
    }}
    .wrap {{
      max-width: 1400px;
      margin: 0 auto;
      display: grid;
      gap: 18px;
    }}
    .card {{
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid #d8d1c7;
      border-radius: 18px;
      padding: 22px;
      box-shadow: 0 10px 28px rgba(80, 66, 45, 0.08);
    }}
    h1, h2 {{
      margin-top: 0;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    img {{
      width: 100%;
      border: 1px solid #d8d1c7;
      border-radius: 14px;
      background: white;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.95rem;
    }}
    th, td {{
      padding: 8px 10px;
      border-bottom: 1px solid #e6dfd5;
      text-align: right;
    }}
    th:first-child, td:first-child {{
      text-align: left;
    }}
    code {{
      font-family: "Iosevka Web", "SFMono-Regular", monospace;
      font-size: 0.92rem;
    }}
    ul {{
      margin: 0;
      padding-left: 18px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="card">
      <h1>GUPS Always-vs-Never Sanity Check</h1>
      <p>This dashboard compares the repo-managed single-node GUPS benchmark under <code>THP always</code> and <code>THP never</code> for <code>{escape(label)}</code>.</p>
      <p>The GUPS process THP series use the latest <code>smaps_rollup_samples</code> row at or before each repeat end. The perf values are repeat-window cumulative deltas from the existing <code>dtlb_misses</code> and <code>dtlb_loads</code> tables. When process-local rows exist for the GUPS TGID they are used; otherwise the renderer falls back to the existing system-wide <code>tgid=0</code> rows.</p>
    </section>
    <section class="card">
      <h2>Inputs</h2>
      <ul>
        <li><strong>Always collection:</strong> <code>{escape(always.collection_id)}</code> (<code>{escape(always.thp_mode)}</code>)</li>
        <li><strong>Never collection:</strong> <code>{escape(never.collection_id)}</code> (<code>{escape(never.thp_mode)}</code>)</li>
        <li><strong>Always run dir:</strong> <code>{escape(always.run_dir.as_posix())}</code></li>
        <li><strong>Never run dir:</strong> <code>{escape(never.run_dir.as_posix())}</code></li>
        <li><strong>Always dTLB misses scope:</strong> <code>{escape(always.dtlb_misses_scope)}</code></li>
        <li><strong>Never dTLB misses scope:</strong> <code>{escape(never.dtlb_misses_scope)}</code></li>
        <li><strong>Always dTLB loads scope:</strong> <code>{escape(always.dtlb_loads_scope)}</code></li>
        <li><strong>Never dTLB loads scope:</strong> <code>{escape(never.dtlb_loads_scope)}</code></li>
      </ul>
    </section>
    <section class="grid">
      <article class="card">
        <h2>GUP/s By Repeat</h2>
        <img src="gups_by_repeat.png" alt="GUP/s by repeat" />
      </article>
      <article class="card">
        <h2>Runtime By Repeat</h2>
        <img src="runtime_by_repeat.png" alt="Runtime by repeat" />
      </article>
      <article class="card">
        <h2>AnonHugePages GiB At Repeat End</h2>
        <img src="anon_hugepages_gib_by_repeat.png" alt="Process AnonHugePages GiB at repeat end" />
      </article>
      <article class="card">
        <h2>dTLB Misses By Repeat</h2>
        <img src="dtlb_misses_by_repeat.png" alt="dTLB misses by repeat" />
      </article>
      <article class="card">
        <h2>dTLB Loads By Repeat</h2>
        <img src="dtlb_loads_by_repeat.png" alt="dTLB loads by repeat" />
      </article>
    </section>
    <section class="card">
      <h2>Exact Repeat Values</h2>
      <table>
        <thead>
          <tr>
            <th>Repeat</th>
            <th>Always GUP/s</th>
            <th>Never GUP/s</th>
            <th>Always runtime (s)</th>
            <th>Never runtime (s)</th>
            <th>Always AnonHugePages GiB</th>
            <th>Never AnonHugePages GiB</th>
            <th>Always dTLB misses</th>
            <th>Never dTLB misses</th>
            <th>Always dTLB loads</th>
            <th>Never dTLB loads</th>
            <th>Always verify</th>
            <th>Never verify</th>
          </tr>
        </thead>
        <tbody>
          {row_html}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8")
    return html_path


def write_config_markdown(
    *,
    output_dir: Path,
    label: str,
    always_stdout: Path,
    never_stdout: Path,
    always_config: Path,
    never_config: Path,
    always: CollectionSeries,
    never: CollectionSeries,
) -> Path:
    """Write the experiment ``config.md`` file alongside the dashboard artifacts."""

    config_path = output_dir / "config.md"
    config_path.write_text(
        "\n".join(
            [
                f"# GUPS Sanity Check {label}",
                "",
                "## Configs",
                f"- always: `{always_config.resolve()}`",
                f"- never: `{never_config.resolve()}`",
                "",
                "## Stdout Logs",
                f"- always: `{always_stdout.resolve()}`",
                f"- never: `{never_stdout.resolve()}`",
                "",
                "## Collections",
                f"- always collection id: `{always.collection_id}`",
                f"- never collection id: `{never.collection_id}`",
                f"- always dtlb_misses scope: `{always.dtlb_misses_scope}`",
                f"- never dtlb_misses scope: `{never.dtlb_misses_scope}`",
                f"- always dtlb_loads scope: `{always.dtlb_loads_scope}`",
                f"- never dtlb_loads scope: `{never.dtlb_loads_scope}`",
                "",
                "## Run Directories",
                f"- always: `{always.run_dir}`",
                f"- never: `{never.run_dir}`",
                "",
                "## Inputs Used For Graphs",
                f"- `{always.run_dir / 'gups_results.jsonl'}`",
                f"- `{never.run_dir / 'gups_results.jsonl'}`",
                f"- `{always.run_dir}/system_info.*.parquet`",
                f"- `{never.run_dir}/system_info.*.parquet`",
                f"- `{always.run_dir}/process_trace.*.parquet`",
                f"- `{never.run_dir}/process_trace.*.parquet`",
                f"- `{always.run_dir}/smaps_rollup_samples.*.parquet`",
                f"- `{never.run_dir}/smaps_rollup_samples.*.parquet`",
                f"- `{always.run_dir}/dtlb_misses.*.parquet`",
                f"- `{never.run_dir}/dtlb_misses.*.parquet`",
                f"- `{always.run_dir}/dtlb_loads.*.parquet`",
                f"- `{never.run_dir}/dtlb_loads.*.parquet`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def main(argv: list[str] | None = None) -> None:
    """Render the GUPS sanity-check dashboard and PNGs."""

    del argv
    args = parse_args()
    output_dir = (args.output_dir / f"gups_sanity_{args.label}").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    always = load_collection_series(
        mode_label="THP always",
        color=ALWAYS_COLOR,
        stdout_log_path=args.always_stdout.resolve(),
        data_root=args.data_root.resolve(),
    )
    never = load_collection_series(
        mode_label="THP never",
        color=NEVER_COLOR,
        stdout_log_path=args.never_stdout.resolve(),
        data_root=args.data_root.resolve(),
    )

    repeat_labels = [repeat.repeat_index for repeat in always.repeats]
    rows = build_table_rows(always, never)

    plot_metric(
        output_path=output_dir / "gups_by_repeat.png",
        title="GUPS Benchmark: GUP/s By Repeat",
        ylabel="GUP/s",
        x_values=repeat_labels,
        always_values=[repeat.gups for repeat in always.repeats],
        never_values=[repeat.gups for repeat in never.repeats],
    )
    plot_metric(
        output_path=output_dir / "runtime_by_repeat.png",
        title="GUPS Benchmark: Runtime By Repeat",
        ylabel="Runtime (sec)",
        x_values=repeat_labels,
        always_values=[repeat.runtime_s for repeat in always.repeats],
        never_values=[repeat.runtime_s for repeat in never.repeats],
    )
    plot_metric(
        output_path=output_dir / "anon_hugepages_gib_by_repeat.png",
        title="GUPS Benchmark: Process AnonHugePages GiB At Repeat End",
        ylabel="AnonHugePages (GiB)",
        x_values=repeat_labels,
        always_values=always.anon_hugepages_gib,
        never_values=never.anon_hugepages_gib,
    )
    plot_metric(
        output_path=output_dir / "dtlb_misses_by_repeat.png",
        title="GUPS Benchmark: dTLB Misses By Repeat",
        ylabel="Misses (cumulative delta)",
        x_values=repeat_labels,
        always_values=always.dtlb_misses,
        never_values=never.dtlb_misses,
    )
    plot_metric(
        output_path=output_dir / "dtlb_loads_by_repeat.png",
        title="GUPS Benchmark: dTLB Loads By Repeat",
        ylabel="Loads (cumulative delta)",
        x_values=repeat_labels,
        always_values=always.dtlb_loads,
        never_values=never.dtlb_loads,
    )
    write_dashboard(
        output_dir=output_dir,
        label=args.label,
        always=always,
        never=never,
        rows=rows,
    )
    write_config_markdown(
        output_dir=output_dir,
        label=args.label,
        always_stdout=args.always_stdout.resolve(),
        never_stdout=args.never_stdout.resolve(),
        always_config=args.always_config.resolve(),
        never_config=args.never_config.resolve(),
        always=always,
        never=never,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render always-vs-never Redis runtime and RSS charts by outer iteration.

This script reads two preserved `collect` stdout logs, resolves their
collection IDs, loads the matching curated Redis run directories, and writes
three PNG charts plus one HTML dashboard:

1. YCSB load runtime by outer iteration.
2. YCSB run runtime by outer iteration.
3. Redis RSS at each run completion by outer iteration.

The runtime series come from `redis_benchmark.log`. The RSS series is rebuilt
from the `mm_rss_stat` event stream for the Redis TGID discovered from
`process_trace`.
"""

from __future__ import annotations

import argparse
import bisect
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import polars as pl


REPO_ROOT = Path("/users/SahilSC/HugePageResearch")
DATA_ROOT = REPO_ROOT / "data" / "curated" / "redis"
OUTPUT_ROOT = REPO_ROOT / "temp_data_analysis"

COLLECTION_ID_RE = re.compile(r"^Collection_id:\s+(\S+)$", re.MULTILINE)
BENCHMARK_START_RE = re.compile(
    r"^\[(?P<clock>\d{2}:\d{2}:\d{2})\]\s+Redis benchmark started at "
    r"(?P<start>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})$"
)
OUTER_REPEAT_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+outer_repeat:\s+(\d+)$")
REPEAT_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+repeat:\s+(\d+)$")
LOAD_START_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+Load phase out_i=(\d+) starting")
RUN_START_RE = re.compile(
    r"^\[\d{2}:\d{2}:\d{2}\]\s+Run phase out_i=(\d+)\s+i=(\d+)\s+starting"
)
LOAD_FINISH_RE = re.compile(
    r"^\[(?P<clock>\d{2}:\d{2}:\d{2})\]\s+Load phase out_i=(?P<outer>\d+) "
    r"finished in (?P<seconds>[\d.]+)s \(exit=(?P<exit>\d+)\)$"
)
RUN_FINISH_RE = re.compile(
    r"^\[(?P<clock>\d{2}:\d{2}:\d{2})\]\s+Run phase out_i=(?P<outer>\d+) "
    r"i=(?P<repeat>\d+) finished in (?P<seconds>[\d.]+)s \(exit=(?P<exit>\d+)\)$"
)
YCSB_RUNTIME_RE = re.compile(r"^\[OVERALL\], RunTime\(ms\), (\d+)$")
YCSB_PROGRESS_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) "
    r"(?P<time>\d{2}:\d{2}:\d{2}):(?P<millis>\d{3}) "
    r"\d+\s+sec:"
)

ALWAYS_COLOR = "#2a9d8f"
NEVER_COLOR = "#e76f51"
FIGSIZE = (12.8, 7.68)
PAGES_TO_GIB = 4096.0 / float(1024**3)


@dataclass(frozen=True)
class PhaseRuntimeSeries:
    """Per-outer-iteration runtime and run-finish timestamps for one run.

    Attributes:
        collection_id: Curated run directory name.
        run_dir: Directory containing the curated parquet and benchmark log.
        load_runtime_s: YCSB load runtime in seconds for each `out_i`.
        run_runtime_s: YCSB run runtime in seconds for each `out_i`.
        run_finish_epoch_s: Epoch-second timestamp for each run completion.
    """

    collection_id: str
    run_dir: Path
    load_runtime_s: list[float]
    run_runtime_s: list[float]
    run_finish_epoch_s: list[float]


@dataclass(frozen=True)
class RSSPoint:
    """One reconstructed Redis RSS sample.

    Attributes:
        ts_epoch_s: Event timestamp converted to epoch seconds.
        rss_pages: Redis resident-set size in 4 KiB pages.

    Example output:
        {"ts_epoch_s": 1775810238.912, "rss_pages": 1050376}
    """

    ts_epoch_s: float
    rss_pages: int


@dataclass(frozen=True)
class CollectionSeries:
    """All graphable metrics for one THP mode.

    Attributes:
        mode_label: Human-readable series label such as `THP always`.
        color: Line color used across all plots.
        collection_id: Curated run directory name.
        run_dir: Curated run directory path.
        load_runtime_s: YCSB load runtime by outer iteration.
        run_runtime_s: YCSB run runtime by outer iteration.
        rss_gib: Redis RSS in GiB at each run completion by outer iteration.
    """

    mode_label: str
    color: str
    collection_id: str
    run_dir: Path
    load_runtime_s: list[float]
    run_runtime_s: list[float]
    rss_gib: list[float]


def parse_args() -> argparse.Namespace:
    """Return CLI arguments for the always-vs-never renderer.

    Returns:
        Parsed CLI arguments with stdout logs, label, and output directory.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--never-stdout",
        required=True,
        type=Path,
        help="Path to the preserved `collect` stdout log for THP never.",
    )
    parser.add_argument(
        "--always-stdout",
        required=True,
        type=Path,
        help="Path to the preserved `collect` stdout log for THP always.",
    )
    parser.add_argument(
        "--label",
        required=True,
        help="Stable artifact label such as 20260413.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_ROOT,
        help="Directory where the PNG and HTML artifacts should be written.",
    )
    return parser.parse_args()


def parse_collection_id(stdout_log_path: Path) -> str:
    """Extract the collection ID from one preserved `collect` stdout log.

    Args:
        stdout_log_path: File created by `tee` during `collect`.

    Returns:
        The collection identifier printed by `collect`.

    Raises:
        RuntimeError: The stdout log does not contain a collection ID.
    """

    raw = stdout_log_path.read_text(encoding="utf-8")
    match = COLLECTION_ID_RE.search(raw)
    if match is None:
        raise RuntimeError(f"Missing Collection_id in {stdout_log_path}")
    return match.group(1)


def chunk_sort_key(file_path: Path) -> tuple[int, int | str]:
    """Return a stable sort key for chunked parquet filenames.

    Args:
        file_path: Parquet file path inside one curated run directory.

    Returns:
        Sort key that keeps numeric chunks in order and `.end` last.
    """

    parts = file_path.name.split(".")
    if len(parts) >= 3:
        chunk = parts[-2]
        if chunk == "end":
            return (1, 0)
        if chunk.isdigit():
            return (0, int(chunk))
    return (0, file_path.name)


def load_table(run_dir: Path, table_name: str) -> pl.DataFrame:
    """Load one chunked parquet table from a curated run directory.

    Args:
        run_dir: Curated Redis run directory.
        table_name: Table stem such as `mm_rss_stat`.

    Returns:
        Concatenated parquet rows for that table.

    Raises:
        RuntimeError: No parquet files exist for the requested table.
    """

    parquet_files = sorted(
        run_dir.glob(f"{table_name}.*.parquet"),
        key=chunk_sort_key,
    )
    if not parquet_files:
        raise RuntimeError(f"Missing {table_name} parquet files in {run_dir}")
    return pl.concat(
        [pl.read_parquet(file_path) for file_path in parquet_files],
        how="diagonal_relaxed",
    )


def parse_line_clock(raw_line: str) -> str | None:
    """Return the `[HH:MM:SS]` prefix from one benchmark-log line.

    Args:
        raw_line: Raw log line from `redis_benchmark.log`.

    Returns:
        Time-of-day string when the line uses the benchmark logger, else `None`.
    """

    if len(raw_line) >= 10 and raw_line.startswith("[") and raw_line[9] == "]":
        return raw_line[1:9]
    return None


def parse_log_epoch(clock_text: str, *, current_dt: datetime) -> datetime:
    """Convert one benchmark-log `[HH:MM:SS]` prefix into a full datetime.

    Args:
        clock_text: Time-of-day portion from the log line.
        current_dt: Most recent parsed datetime from the same log stream.

    Returns:
        Full datetime for this log line, rolling into the next day if needed.
    """

    candidate = datetime.combine(
        current_dt.date(),
        datetime.strptime(clock_text, "%H:%M:%S").time(),
    )
    if candidate < current_dt:
        candidate += timedelta(days=1)
    return candidate


def parse_runtime_series(collection_id: str) -> PhaseRuntimeSeries:
    """Parse YCSB runtime and run-finish timestamps for one collection.

    Args:
        collection_id: Curated Redis collection directory name.

    Returns:
        Per-outer-iteration load/runtime values and run-finish timestamps.

    Raises:
        RuntimeError: The benchmark log is malformed or does not match the
            expected `outer_repeat=20`, `repeat=1` layout.
    """

    run_dir = DATA_ROOT / collection_id
    benchmark_log_path = run_dir / "redis_benchmark.log"
    if not benchmark_log_path.is_file():
        raise RuntimeError(f"Missing benchmark log: {benchmark_log_path}")

    lines = benchmark_log_path.read_text(encoding="utf-8").splitlines()
    start_match = None
    for raw_line in lines:
        start_match = BENCHMARK_START_RE.match(raw_line)
        if start_match is not None:
            break
    if start_match is None:
        raise RuntimeError(f"Missing benchmark start line in {benchmark_log_path}")

    current_dt = datetime.strptime(start_match.group("start"), "%Y-%m-%d %H:%M:%S")
    outer_repeat: int | None = None
    repeat_count: int | None = None
    active_phase: tuple[str, int] | None = None
    active_phase_progress_epoch_s: float | None = None
    load_runtime_ms: dict[int, int] = {}
    run_runtime_ms: dict[int, int] = {}
    run_finish_epoch_s: dict[int, float] = {}

    for raw_line in lines:
        clock_text = parse_line_clock(raw_line)
        if clock_text is not None:
            current_dt = parse_log_epoch(clock_text, current_dt=current_dt)

        if outer_repeat is None:
            outer_match = OUTER_REPEAT_RE.match(raw_line)
            if outer_match:
                outer_repeat = int(outer_match.group(1))
                continue
        if repeat_count is None:
            repeat_match = REPEAT_RE.match(raw_line)
            if repeat_match:
                repeat_count = int(repeat_match.group(1))
                continue

        load_start_match = LOAD_START_RE.match(raw_line)
        if load_start_match:
            active_phase = ("load", int(load_start_match.group(1)))
            active_phase_progress_epoch_s = None
            continue

        run_start_match = RUN_START_RE.match(raw_line)
        if run_start_match:
            repeat_index = int(run_start_match.group(2))
            if repeat_index != 0:
                raise RuntimeError(
                    f"{benchmark_log_path} expected repeat index 0, found {repeat_index}"
                )
            active_phase = ("run", int(run_start_match.group(1)))
            active_phase_progress_epoch_s = None
            continue

        progress_match = YCSB_PROGRESS_RE.match(raw_line)
        if progress_match and active_phase is not None:
            active_phase_progress_epoch_s = datetime.strptime(
                (
                    f"{progress_match.group('date')} "
                    f"{progress_match.group('time')}."
                    f"{progress_match.group('millis')}"
                ),
                "%Y-%m-%d %H:%M:%S.%f",
            ).timestamp()
            continue

        runtime_match = YCSB_RUNTIME_RE.match(raw_line)
        if runtime_match:
            if active_phase is None:
                raise RuntimeError(
                    f"{benchmark_log_path} saw RunTime(ms) without an active phase"
                )
            phase_name, outer_index = active_phase
            runtime_ms = int(runtime_match.group(1))
            if phase_name == "load":
                load_runtime_ms[outer_index] = runtime_ms
            else:
                run_runtime_ms[outer_index] = runtime_ms
                if active_phase_progress_epoch_s is None:
                    raise RuntimeError(
                        f"{benchmark_log_path} missing timestamped YCSB progress line "
                        f"before run runtime for out_i={outer_index}"
                    )
                run_finish_epoch_s[outer_index] = active_phase_progress_epoch_s
            active_phase = None
            active_phase_progress_epoch_s = None
            continue

        run_finish_match = RUN_FINISH_RE.match(raw_line)
        if run_finish_match:
            if int(run_finish_match.group("repeat")) != 0:
                raise RuntimeError(
                    f"{benchmark_log_path} saw unexpected run finish repeat index "
                    f"{run_finish_match.group('repeat')}"
                )
            if int(run_finish_match.group("exit")) != 0:
                raise RuntimeError(
                    f"{benchmark_log_path} saw non-zero run exit for out_i="
                    f"{run_finish_match.group('outer')}"
                )
            run_finish_epoch_s[int(run_finish_match.group("outer"))] = current_dt.timestamp()
            continue

        load_finish_match = LOAD_FINISH_RE.match(raw_line)
        if load_finish_match and int(load_finish_match.group("exit")) != 0:
            raise RuntimeError(
                f"{benchmark_log_path} saw non-zero load exit for out_i="
                f"{load_finish_match.group('outer')}"
            )

    if outer_repeat is None:
        raise RuntimeError(f"{benchmark_log_path} is missing outer_repeat")
    if outer_repeat != 20:
        raise RuntimeError(
            f"{benchmark_log_path} expected outer_repeat=20, found {outer_repeat}"
        )
    if repeat_count is None:
        raise RuntimeError(f"{benchmark_log_path} is missing repeat")
    if repeat_count != 1:
        raise RuntimeError(
            f"{benchmark_log_path} expected repeat=1, found {repeat_count}"
        )

    expected_outer = list(range(outer_repeat))
    missing_load = [outer for outer in expected_outer if outer not in load_runtime_ms]
    missing_run = [outer for outer in expected_outer if outer not in run_runtime_ms]
    missing_finish = [outer for outer in expected_outer if outer not in run_finish_epoch_s]
    if missing_load:
        raise RuntimeError(
            f"{benchmark_log_path} missing load runtimes for outer iterations {missing_load}"
        )
    if missing_run:
        raise RuntimeError(
            f"{benchmark_log_path} missing run runtimes for outer iterations {missing_run}"
        )
    if missing_finish:
        raise RuntimeError(
            f"{benchmark_log_path} missing run finish times for outer iterations {missing_finish}"
        )

    return PhaseRuntimeSeries(
        collection_id=collection_id,
        run_dir=run_dir,
        load_runtime_s=[load_runtime_ms[outer] / 1000.0 for outer in expected_outer],
        run_runtime_s=[run_runtime_ms[outer] / 1000.0 for outer in expected_outer],
        run_finish_epoch_s=[run_finish_epoch_s[outer] for outer in expected_outer],
    )


def discover_redis_tgid(process_trace_df: pl.DataFrame) -> int:
    """Return the single Redis TGID from `process_trace`.

    Args:
        process_trace_df: Concatenated `process_trace` parquet rows.

    Returns:
        The unique Redis TGID for this collection.

    Raises:
        RuntimeError: Redis TGID discovery is ambiguous or empty.
    """

    if "name" not in process_trace_df.columns or "tgid" not in process_trace_df.columns:
        raise RuntimeError("process_trace is missing required `name` or `tgid` columns")
    redis_rows = process_trace_df.filter(pl.col("name").str.starts_with("redis-server"))
    if redis_rows.is_empty():
        raise RuntimeError("process_trace does not contain a redis-server TGID")
    tgids = sorted(redis_rows.select("tgid").unique().to_series().to_list())
    if len(tgids) != 1:
        raise RuntimeError(f"Expected exactly one Redis TGID, found {tgids}")
    return int(tgids[0])


def reconstruct_rss_points(run_dir: Path, redis_tgid: int) -> list[RSSPoint]:
    """Rebuild Redis RSS over time from `mm_rss_stat`.

    Args:
        run_dir: Curated Redis run directory.
        redis_tgid: Target Redis thread-group ID.

    Returns:
        Ordered RSS points reconstructed from the current-value event stream.

    Raises:
        RuntimeError: The run lacks usable RSS events or boot-time metadata.
    """

    rss_df = load_table(run_dir, "mm_rss_stat")
    rss_df = rss_df.filter(pl.col("tgid") == redis_tgid)
    if rss_df.is_empty():
        raise RuntimeError(f"{run_dir} has no mm_rss_stat rows for Redis TGID {redis_tgid}")

    system_info_path = run_dir / "system_info.end.parquet"
    if not system_info_path.is_file():
        raise RuntimeError(f"Missing system info parquet: {system_info_path}")
    system_info = pl.read_parquet(system_info_path)
    if "start_time_sec" not in system_info.columns:
        raise RuntimeError(f"{system_info_path} is missing start_time_sec")
    boot_time_sec = float(system_info["start_time_sec"][0])

    ordered = (
        rss_df.with_row_index("row_idx")
        .sort(["ts_ns", "row_idx"])
        .select(["ts_ns", "member", "count"])
        .iter_rows(named=True)
    )
    state_pages = {
        "MM_FILEPAGES": 0,
        "MM_ANONPAGES": 0,
        "MM_SHMEMPAGES": 0,
    }
    points: list[RSSPoint] = []
    for row in ordered:
        member = str(row["member"])
        if member not in state_pages:
            continue
        state_pages[member] = int(row["count"])
        rss_pages = (
            state_pages["MM_FILEPAGES"]
            + state_pages["MM_ANONPAGES"]
            + state_pages["MM_SHMEMPAGES"]
        )
        points.append(
            RSSPoint(
                ts_epoch_s=boot_time_sec + (int(row["ts_ns"]) / 1_000_000_000.0),
                rss_pages=rss_pages,
            )
        )

    if not points:
        raise RuntimeError(f"{run_dir} did not emit MM_FILEPAGES/MM_ANONPAGES/MM_SHMEMPAGES")
    return points


def rss_at_or_before(points: list[RSSPoint], target_epoch_s: float) -> float:
    """Return the latest RSS in GiB at or before one phase-finish timestamp.

    Args:
        points: Ordered RSS points for the Redis process.
        target_epoch_s: Run-finish timestamp in epoch seconds.

    Returns:
        RSS value converted to GiB.

    Raises:
        RuntimeError: There is no RSS point at or before the target time.
    """

    timestamps = [point.ts_epoch_s for point in points]
    position = bisect.bisect_right(timestamps, target_epoch_s) - 1
    if position < 0:
        raise RuntimeError(
            f"No RSS sample exists at or before run completion time {target_epoch_s:.3f}"
        )
    return points[position].rss_pages * PAGES_TO_GIB


def load_collection_series(
    *,
    mode_label: str,
    color: str,
    stdout_log_path: Path,
) -> CollectionSeries:
    """Load all graphable metrics for one preserved collect stdout log.

    Args:
        mode_label: Human-readable line label.
        color: Plot color for this line.
        stdout_log_path: Preserved `collect` stdout log.

    Returns:
        Combined runtime and RSS series for the matching collection.
    """

    collection_id = parse_collection_id(stdout_log_path)
    runtime_series = parse_runtime_series(collection_id)
    process_trace_df = load_table(runtime_series.run_dir, "process_trace")
    redis_tgid = discover_redis_tgid(process_trace_df)
    rss_points = reconstruct_rss_points(runtime_series.run_dir, redis_tgid)
    rss_gib = [
        rss_at_or_before(rss_points, finish_epoch_s)
        for finish_epoch_s in runtime_series.run_finish_epoch_s
    ]
    return CollectionSeries(
        mode_label=mode_label,
        color=color,
        collection_id=collection_id,
        run_dir=runtime_series.run_dir,
        load_runtime_s=runtime_series.load_runtime_s,
        run_runtime_s=runtime_series.run_runtime_s,
        rss_gib=rss_gib,
    )


def plot_series(
    *,
    output_path: Path,
    title: str,
    ylabel: str,
    series_map: dict[str, tuple[list[float], str]],
) -> None:
    """Write one two-line outer-iteration chart to PNG.

    Args:
        output_path: PNG file to write.
        title: Chart title.
        ylabel: Y-axis label.
        series_map: Mapping from human-readable label to `(values, color)`.
    """

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=100)
    for label, (values, color) in series_map.items():
        x_values = list(range(len(values)))
        ax.plot(
            x_values,
            values,
            label=label,
            color=color,
            marker="o",
            linewidth=2.8,
            markersize=10,
            alpha=0.9,
        )

    ax.set_title(title, fontsize=24, pad=12)
    ax.set_xlabel("Outer iteration (out_i)", fontsize=20)
    ax.set_ylabel(ylabel, fontsize=20)
    ax.tick_params(axis="both", labelsize=18)
    ax.legend(fontsize=20, loc="upper left", framealpha=0.9)
    ax.grid(True, color="#c8cfd9", alpha=0.55)
    fig.tight_layout()
    fig.savefig(output_path, dpi=100)
    plt.close(fig)


def build_summary_table(always: CollectionSeries, never: CollectionSeries) -> str:
    """Return one HTML table summarizing all parsed outer-iteration values.

    Args:
        always: THP always series.
        never: THP never series.

    Returns:
        HTML string containing the summary table.
    """

    rows: list[str] = []
    for outer_index in range(len(always.load_runtime_s)):
        rows.append(
            "<tr>"
            f"<td>{outer_index}</td>"
            f"<td>{always.load_runtime_s[outer_index]:.3f}</td>"
            f"<td>{always.run_runtime_s[outer_index]:.3f}</td>"
            f"<td>{always.rss_gib[outer_index]:.3f}</td>"
            f"<td>{never.load_runtime_s[outer_index]:.3f}</td>"
            f"<td>{never.run_runtime_s[outer_index]:.3f}</td>"
            f"<td>{never.rss_gib[outer_index]:.3f}</td>"
            "</tr>"
        )
    return """
    <table>
      <thead>
        <tr>
          <th>out_i</th>
          <th>Always load (s)</th>
          <th>Always run (s)</th>
          <th>Always RSS (GiB)</th>
          <th>Never load (s)</th>
          <th>Never run (s)</th>
          <th>Never RSS (GiB)</th>
        </tr>
      </thead>
      <tbody>
    """ + "".join(rows) + """
      </tbody>
    </table>
    """


def write_dashboard(
    *,
    output_path: Path,
    label: str,
    always: CollectionSeries,
    never: CollectionSeries,
    load_png: Path,
    run_png: Path,
    rss_png: Path,
    always_stdout: Path,
    never_stdout: Path,
) -> None:
    """Write the browser-friendly dashboard for the rendered PNG artifacts.

    Args:
        output_path: HTML path to write.
        label: Stable artifact label.
        always: THP always series.
        never: THP never series.
        load_png: Load-runtime PNG path.
        run_png: Run-runtime PNG path.
        rss_png: RSS PNG path.
        always_stdout: Preserved always stdout log.
        never_stdout: Preserved never stdout log.
    """

    summary_table = build_summary_table(always, never)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Redis always vs never RSS/runtime {escape(label)}</title>
  <style>
    :root {{
      --bg: #f4f6f9;
      --card: #ffffff;
      --ink: #1b2733;
      --muted: #617181;
      --border: #d7dee7;
    }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(42, 157, 143, 0.12), transparent 30%),
        linear-gradient(180deg, #f8fafc 0%, var(--bg) 100%);
      font-family: Georgia, "Times New Roman", serif;
    }}
    main {{
      max-width: 1320px;
      margin: 0 auto;
      padding: 28px 22px 48px;
    }}
    header {{
      background: linear-gradient(135deg, #18324a 0%, #28536b 55%, #2a9d8f 100%);
      color: #f8fbff;
      border-radius: 24px;
      padding: 28px 30px;
      box-shadow: 0 24px 70px rgba(24, 50, 74, 0.22);
      margin-bottom: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 34px;
      line-height: 1.1;
    }}
    header p {{
      margin: 6px 0;
      color: rgba(248, 251, 255, 0.92);
      font-size: 16px;
      line-height: 1.45;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .card {{
      background: var(--card);
      border-radius: 20px;
      border: 1px solid rgba(215, 222, 231, 0.9);
      box-shadow: 0 18px 48px rgba(26, 38, 53, 0.08);
      padding: 18px;
      margin-top: 18px;
    }}
    .metric-label {{
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
      font-family: "Helvetica Neue", Arial, sans-serif;
    }}
    .metric-value {{
      font-size: 28px;
      font-weight: 700;
      font-family: "Helvetica Neue", Arial, sans-serif;
    }}
    .metric-sub {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
      font-family: "Helvetica Neue", Arial, sans-serif;
    }}
    h2 {{
      margin: 0 0 4px;
      font-size: 22px;
    }}
    p.subtitle {{
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
      font-family: "Helvetica Neue", Arial, sans-serif;
    }}
    img {{
      width: 100%;
      border-radius: 16px;
      border: 1px solid var(--border);
    }}
    ul {{
      margin: 0;
      padding-left: 20px;
    }}
    li {{
      margin: 6px 0;
      font-family: "Helvetica Neue", Arial, sans-serif;
    }}
    code {{
      background: #edf2f7;
      border-radius: 6px;
      padding: 2px 6px;
      font-size: 0.95em;
      font-family: "SFMono-Regular", Consolas, monospace;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-family: "Helvetica Neue", Arial, sans-serif;
      font-size: 14px;
    }}
    th, td {{
      padding: 12px 10px;
      text-align: left;
      border-bottom: 1px solid var(--border);
    }}
    th {{
      font-size: 12px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--muted);
      background: #f7fafc;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Redis always vs never RSS/runtime</h1>
      <p>This dashboard compares the two RSS-enabled collections captured on <code>{escape(label)}</code>.</p>
      <p>The runtime graphs use raw YCSB <code>[OVERALL], RunTime(ms)</code> values by <code>out_i</code>. The RSS graph uses the latest reconstructed Redis RSS sample at or before each run-phase completion.</p>
    </header>

    <div class="grid">
      <section class="card">
        <div class="metric-label">Always Collection</div>
        <div class="metric-value">{escape(always.collection_id)}</div>
        <div class="metric-sub">{escape(always.run_dir.as_posix())}</div>
      </section>
      <section class="card">
        <div class="metric-label">Never Collection</div>
        <div class="metric-value">{escape(never.collection_id)}</div>
        <div class="metric-sub">{escape(never.run_dir.as_posix())}</div>
      </section>
      <section class="card">
        <div class="metric-label">Always Stdout Log</div>
        <div class="metric-value">tee output</div>
        <div class="metric-sub">{escape(always_stdout.as_posix())}</div>
      </section>
      <section class="card">
        <div class="metric-label">Never Stdout Log</div>
        <div class="metric-value">tee output</div>
        <div class="metric-sub">{escape(never_stdout.as_posix())}</div>
      </section>
    </div>

    <section class="card">
      <h2>Load runtime by outer iteration</h2>
      <p class="subtitle">Same visual style as the prior always-vs-never YCSB runtime overlays, now regenerated from the new RSS-enabled collections.</p>
      <img src="{escape(load_png.name)}" alt="Load runtime by outer iteration" />
    </section>

    <section class="card">
      <h2>Run runtime by outer iteration</h2>
      <p class="subtitle">Per-outer-iteration run-phase [OVERALL] runtime from <code>redis_benchmark.log</code>.</p>
      <img src="{escape(run_png.name)}" alt="Run runtime by outer iteration" />
    </section>

    <section class="card">
      <h2>Redis RSS at run completion</h2>
      <p class="subtitle">Each point is the latest Redis RSS value rebuilt from <code>mm_rss_stat</code> at or before the matching run completion time.</p>
      <img src="{escape(rss_png.name)}" alt="Redis RSS by outer iteration" />
    </section>

    <section class="card">
      <h2>Exact inputs used</h2>
      <ul>
        <li><code>{escape(always.run_dir.as_posix())}/redis_benchmark.log</code></li>
        <li><code>{escape(always.run_dir.as_posix())}/process_trace.*.parquet</code></li>
        <li><code>{escape(always.run_dir.as_posix())}/mm_rss_stat.*.parquet</code></li>
        <li><code>{escape(never.run_dir.as_posix())}/redis_benchmark.log</code></li>
        <li><code>{escape(never.run_dir.as_posix())}/process_trace.*.parquet</code></li>
        <li><code>{escape(never.run_dir.as_posix())}/mm_rss_stat.*.parquet</code></li>
      </ul>
    </section>

    <section class="card">
      <h2>Parsed values</h2>
      <p class="subtitle">These are the exact outer-iteration values used to draw the PNG charts.</p>
      {summary_table}
    </section>
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def main() -> None:
    """Load the two collections and render the requested artifacts."""

    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    always = load_collection_series(
        mode_label="THP always",
        color=ALWAYS_COLOR,
        stdout_log_path=args.always_stdout,
    )
    never = load_collection_series(
        mode_label="THP never",
        color=NEVER_COLOR,
        stdout_log_path=args.never_stdout,
    )

    load_png = output_dir / f"ycsb_overall_load_runtime_{args.label}_always_vs_never.png"
    run_png = output_dir / f"ycsb_overall_run_runtime_{args.label}_always_vs_never.png"
    rss_png = output_dir / f"redis_rss_outer_iteration_{args.label}_always_vs_never.png"
    html_path = output_dir / f"redis_rss_runtime_{args.label}_always_vs_never.html"

    plot_series(
        output_path=load_png,
        title="Redis Load Runtime Over Time (YCSB [OVERALL])",
        ylabel="YCSB [OVERALL] runtime (s)",
        series_map={
            always.mode_label: (always.load_runtime_s, always.color),
            never.mode_label: (never.load_runtime_s, never.color),
        },
    )
    plot_series(
        output_path=run_png,
        title="Redis Run Runtime Over Time (YCSB [OVERALL])",
        ylabel="YCSB [OVERALL] runtime (s)",
        series_map={
            always.mode_label: (always.run_runtime_s, always.color),
            never.mode_label: (never.run_runtime_s, never.color),
        },
    )
    plot_series(
        output_path=rss_png,
        title="Redis RSS At Run Completion Over Time",
        ylabel="Redis RSS (GiB)",
        series_map={
            always.mode_label: (always.rss_gib, always.color),
            never.mode_label: (never.rss_gib, never.color),
        },
    )
    write_dashboard(
        output_path=html_path,
        label=args.label,
        always=always,
        never=never,
        load_png=load_png,
        run_png=run_png,
        rss_png=rss_png,
        always_stdout=args.always_stdout,
        never_stdout=args.never_stdout,
    )

    print(load_png)
    print(run_png)
    print(rss_png)
    print(html_path)


if __name__ == "__main__":
    main()

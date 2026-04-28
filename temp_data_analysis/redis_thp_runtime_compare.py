#!/usr/bin/env python3
"""Compare Redis THP runtime across the two outer-repeat=10 suites."""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = ROOT / "python" / "kernmlops"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from experiments.redis_thp_replication.reporting import grouped_bar_png, write_html_report


COLLECTION_ID_RE = re.compile(r"^Collection_id:\s+(\S+)$", re.MULTILINE)
LOAD_PHASE_RE = re.compile(r"Load phase out_i=\d+ starting")
RUN_PHASE_RE = re.compile(r"Run phase out_i=\d+ i=\d+ starting")
YCSB_RUNTIME_RE = re.compile(r"^\[OVERALL\], RunTime\(ms\), (\d+)$")

OUTER10_PNG_PATH = ROOT / "temp_data_analysis" / "redis_thp_outer10_runtime_compare.png"
UPDATE50_PNG_PATH = (
    ROOT / "temp_data_analysis" / "redis_thp_outer10_update50_delete50_runtime_compare.png"
)
DASHBOARD_PATH = ROOT / "temp_data_analysis" / "redis_thp_runtime_compare_dashboard.html"
RESULTS_PATH = ROOT / "temp_data_analysis" / "results.md"
CONTAINER_FILE = ROOT / "temp_data_analysis" / "redis_thp_compare_container.txt"
COMMANDS_FILE = ROOT / "temp_data_analysis" / "redis_thp_compare_commands.txt"
MONITOR_LOG_PATH = ROOT / "temp_data_analysis" / "redis_thp_compare_monitor.log"


@dataclass(frozen=True)
class RunSpec:
    """One Redis THP collection run plus its expected artifacts.

    Attributes:
        suite_slug: Stable suite identifier used in filenames.
        suite_label: Human-readable suite label used in reports.
        mode: THP mode for the run.
        mode_label: Human-readable THP mode label for charts.
        color: Series color for grouped bar charts.
        config_path: Repo-relative YAML config path.
        stdout_log_path: Preserved collect stdout log.
        benchmark_log_path: Preserved benchmark wrapper log.
    """

    suite_slug: str
    suite_label: str
    mode: str
    mode_label: str
    color: str
    config_path: Path
    stdout_log_path: Path
    benchmark_log_path: Path

    @property
    def command(self) -> str:
        """Return the exact collect command used for this run.

        Returns:
            The full `python python/kernmlops collect ...` command string.
        """

        return (
            f"python python/kernmlops collect -v -c {self.config_path.as_posix()} "
            "--benchmark redis"
        )


@dataclass(frozen=True)
class ParsedRun:
    """Parsed output metrics for one collected Redis run.

    Attributes:
        suite_slug: Stable suite identifier.
        suite_label: Human-readable suite label.
        mode: THP mode.
        mode_label: Human-readable THP mode label.
        color: Series color used in charts.
        config_path: YAML config path used for the run.
        collection_id: Collected run identifier printed by `collect`.
        total_time_s: `collection_time_sec` from `system_info.end.parquet`.
        load_sum_s: Sum of YCSB load runtimes in seconds.
        run_sum_s: Sum of YCSB run runtimes in seconds.
        load_count: Number of parsed load phases.
        run_count: Number of parsed run phases.
        stdout_log_path: Preserved collect stdout log path.
        benchmark_log_path: Preserved benchmark wrapper log path.
    """

    suite_slug: str
    suite_label: str
    mode: str
    mode_label: str
    color: str
    config_path: Path
    collection_id: str
    total_time_s: float
    load_sum_s: float
    run_sum_s: float
    load_count: int
    run_count: int
    stdout_log_path: Path
    benchmark_log_path: Path


RUN_SPECS = [
    RunSpec(
        suite_slug="outer10",
        suite_label="outer_repeat=10, read/delete mix",
        mode="always",
        mode_label="THP always",
        color="#2874a6",
        config_path=Path("config/redis_always_outer10.yaml"),
        stdout_log_path=ROOT / "temp_data_analysis" / "outer10_always_collect.stdout.log",
        benchmark_log_path=ROOT / "temp_data_analysis" / "outer10_always_redis_benchmark.log",
    ),
    RunSpec(
        suite_slug="outer10",
        suite_label="outer_repeat=10, read/delete mix",
        mode="madvise",
        mode_label="THP madvise",
        color="#2e8b57",
        config_path=Path("config/redis_madvise_outer10.yaml"),
        stdout_log_path=ROOT / "temp_data_analysis" / "outer10_madvise_collect.stdout.log",
        benchmark_log_path=ROOT / "temp_data_analysis" / "outer10_madvise_redis_benchmark.log",
    ),
    RunSpec(
        suite_slug="outer10",
        suite_label="outer_repeat=10, read/delete mix",
        mode="never",
        mode_label="THP never",
        color="#c05a00",
        config_path=Path("config/redis_never_outer10.yaml"),
        stdout_log_path=ROOT / "temp_data_analysis" / "outer10_never_collect.stdout.log",
        benchmark_log_path=ROOT / "temp_data_analysis" / "outer10_never_redis_benchmark.log",
    ),
    RunSpec(
        suite_slug="outer10_update50_delete50",
        suite_label="outer_repeat=10, update/delete 50/50",
        mode="always",
        mode_label="THP always",
        color="#2874a6",
        config_path=Path("config/redis_always_outer10_update50_delete50.yaml"),
        stdout_log_path=ROOT
        / "temp_data_analysis"
        / "outer10_update50_delete50_always_collect.stdout.log",
        benchmark_log_path=ROOT
        / "temp_data_analysis"
        / "outer10_update50_delete50_always_redis_benchmark.log",
    ),
    RunSpec(
        suite_slug="outer10_update50_delete50",
        suite_label="outer_repeat=10, update/delete 50/50",
        mode="madvise",
        mode_label="THP madvise",
        color="#2e8b57",
        config_path=Path("config/redis_madvise_outer10_update50_delete50.yaml"),
        stdout_log_path=ROOT
        / "temp_data_analysis"
        / "outer10_update50_delete50_madvise_collect.stdout.log",
        benchmark_log_path=ROOT
        / "temp_data_analysis"
        / "outer10_update50_delete50_madvise_redis_benchmark.log",
    ),
    RunSpec(
        suite_slug="outer10_update50_delete50",
        suite_label="outer_repeat=10, update/delete 50/50",
        mode="never",
        mode_label="THP never",
        color="#c05a00",
        config_path=Path("config/redis_never_outer10_update50_delete50.yaml"),
        stdout_log_path=ROOT
        / "temp_data_analysis"
        / "outer10_update50_delete50_never_collect.stdout.log",
        benchmark_log_path=ROOT
        / "temp_data_analysis"
        / "outer10_update50_delete50_never_redis_benchmark.log",
    ),
]


def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    """Read one Redis YAML config and return the parsed mapping.

    Args:
        config_path: Repo-relative or absolute config path.

    Returns:
        Parsed YAML content as a dictionary.

    Raises:
        FileNotFoundError: The YAML file does not exist.
        RuntimeError: The YAML file is empty or malformed for this task.
    """

    full_path = config_path if config_path.is_absolute() else ROOT / config_path
    if not full_path.is_file():
        raise FileNotFoundError(f"Config file not found: {full_path}")
    payload = yaml.safe_load(full_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Config file {full_path} did not load as a mapping")
    return payload


def expected_phase_counts(config_path: Path) -> tuple[int, int]:
    """Return expected load and run counts for one Redis config.

    Args:
        config_path: Repo-relative config file path.

    Returns:
        `(load_count, run_count)` derived from `outer_repeat` and `repeat`.

    Raises:
        RuntimeError: Required Redis config values are missing.
    """

    payload = _load_yaml_config(config_path)
    redis_cfg = payload.get("benchmark_config", {}).get("redis", {})
    if not isinstance(redis_cfg, dict):
        raise RuntimeError(f"Missing benchmark_config.redis in {config_path}")
    outer_repeat = int(redis_cfg["outer_repeat"])
    repeat = int(redis_cfg["repeat"])
    return outer_repeat, outer_repeat * repeat


def validate_config_suites(run_specs: list[RunSpec]) -> None:
    """Fail fast if the new config suites are not symmetric or sum incorrectly.

    Args:
        run_specs: All configured runs for the comparison.

    Raises:
        RuntimeError: One suite drifted outside the expected THP-only difference.
    """

    suites: dict[str, list[RunSpec]] = {}
    for run_spec in run_specs:
        suites.setdefault(run_spec.suite_slug, []).append(run_spec)

    for suite_slug, suite_specs in suites.items():
        suite_payloads: list[tuple[str, dict[str, Any]]] = []
        for run_spec in suite_specs:
            payload = _load_yaml_config(run_spec.config_path)
            suite_payloads.append((run_spec.mode, payload))

            benchmark_generic = payload["benchmark_config"]["generic"].copy()
            redis_cfg = payload["benchmark_config"]["redis"]
            benchmark_generic.pop("transparent_hugepages", None)
            operation_total = sum(
                float(redis_cfg[name])
                for name in (
                    "read_proportion",
                    "update_proportion",
                    "insert_proportion",
                    "rmw_proportion",
                    "scan_proportion",
                    "delete_proportion",
                )
            )
            if not math.isclose(operation_total, 1.0, rel_tol=0.0, abs_tol=1e-9):
                raise RuntimeError(
                    f"{run_spec.config_path} operation proportions must sum to 1.0, "
                    f"got {operation_total:.12f}"
                )

        baseline_mode, baseline_payload = suite_payloads[0]
        baseline_generic = baseline_payload["benchmark_config"]["generic"].copy()
        baseline_generic.pop("transparent_hugepages", None)
        baseline_redis = baseline_payload["benchmark_config"]["redis"]
        baseline_collector = baseline_payload["collector_config"]

        for mode, payload in suite_payloads[1:]:
            candidate_generic = payload["benchmark_config"]["generic"].copy()
            candidate_generic.pop("transparent_hugepages", None)
            if candidate_generic != baseline_generic:
                raise RuntimeError(
                    f"{suite_slug} generic config drift between {baseline_mode} and {mode}"
                )
            if payload["benchmark_config"]["redis"] != baseline_redis:
                raise RuntimeError(
                    f"{suite_slug} redis config drift between {baseline_mode} and {mode}"
                )
            if payload["collector_config"] != baseline_collector:
                raise RuntimeError(
                    f"{suite_slug} collector config drift between {baseline_mode} and {mode}"
                )


def parse_collection_id(stdout_log_path: Path) -> str:
    """Extract the `Collection_id` printed by `collect`.

    Args:
        stdout_log_path: Preserved stdout log from one collect run.

    Returns:
        The printed collection identifier.

    Raises:
        RuntimeError: The log does not contain a collection id line.
    """

    match = COLLECTION_ID_RE.search(stdout_log_path.read_text(encoding="utf-8"))
    if match is None:
        raise RuntimeError(f"Missing Collection_id in {stdout_log_path}")
    return match.group(1)


def parse_phase_runtimes(
    benchmark_log_path: Path,
    *,
    expected_load_count: int,
    expected_run_count: int,
) -> dict[str, Any]:
    """Parse YCSB runtimes from the benchmark wrapper log.

    The parser assigns the next `RunTime(ms)` line to the most recent phase
    marker and fails immediately if counts drift or a marker is left unmatched.

    Args:
        benchmark_log_path: Preserved `redis_benchmark.log` copy.
        expected_load_count: Expected number of load phases.
        expected_run_count: Expected number of run phases.

    Returns:
        Dictionary containing parsed per-phase runtimes and sums in seconds.

    Raises:
        RuntimeError: The log is incomplete, malformed, or count-mismatched.
    """

    load_runtimes_ms: list[int] = []
    run_runtimes_ms: list[int] = []
    active_phase: str | None = None

    for line_no, raw_line in enumerate(
        benchmark_log_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if LOAD_PHASE_RE.search(raw_line):
            if active_phase is not None:
                raise RuntimeError(
                    f"{benchmark_log_path}:{line_no} started a new phase before "
                    f"recording the previous {active_phase} runtime"
                )
            active_phase = "load"
            continue

        if RUN_PHASE_RE.search(raw_line):
            if active_phase is not None:
                raise RuntimeError(
                    f"{benchmark_log_path}:{line_no} started a new phase before "
                    f"recording the previous {active_phase} runtime"
                )
            active_phase = "run"
            continue

        runtime_match = YCSB_RUNTIME_RE.match(raw_line.strip())
        if runtime_match is None:
            continue
        if active_phase is None:
            raise RuntimeError(
                f"{benchmark_log_path}:{line_no} saw RunTime(ms) without an active phase"
            )

        runtime_ms = int(runtime_match.group(1))
        if active_phase == "load":
            load_runtimes_ms.append(runtime_ms)
        else:
            run_runtimes_ms.append(runtime_ms)
        active_phase = None

    if active_phase is not None:
        raise RuntimeError(
            f"{benchmark_log_path} ended before the last {active_phase} runtime was recorded"
        )

    if len(load_runtimes_ms) != expected_load_count:
        raise RuntimeError(
            f"{benchmark_log_path} expected {expected_load_count} load runtimes, "
            f"found {len(load_runtimes_ms)}"
        )
    if len(run_runtimes_ms) != expected_run_count:
        raise RuntimeError(
            f"{benchmark_log_path} expected {expected_run_count} run runtimes, "
            f"found {len(run_runtimes_ms)}"
        )

    return {
        "load_runtimes_ms": load_runtimes_ms,
        "run_runtimes_ms": run_runtimes_ms,
        "load_sum_s": sum(load_runtimes_ms) / 1000.0,
        "run_sum_s": sum(run_runtimes_ms) / 1000.0,
        "load_count": len(load_runtimes_ms),
        "run_count": len(run_runtimes_ms),
    }


def read_collection_time_s(collection_id: str) -> float:
    """Read `collection_time_sec` from one curated Redis run.

    Args:
        collection_id: Collection identifier printed by `collect`.

    Returns:
        Total collection runtime in seconds.

    Raises:
        FileNotFoundError: The curated parquet is missing.
        RuntimeError: The parquet does not contain `collection_time_sec`.
    """

    parquet_path = ROOT / "data" / "curated" / "redis" / collection_id / "system_info.end.parquet"
    if not parquet_path.is_file():
        raise FileNotFoundError(f"Missing system_info parquet: {parquet_path}")
    df = pl.read_parquet(parquet_path)
    if "collection_time_sec" not in df.columns:
        raise RuntimeError(f"{parquet_path} does not contain collection_time_sec")
    return float(df["collection_time_sec"][0])


def collect_run_metrics(run_specs: list[RunSpec]) -> list[ParsedRun]:
    """Load all finished run artifacts into parsed metric rows.

    Args:
        run_specs: Ordered run specifications for the comparison.

    Returns:
        Parsed metric rows in the same order as `run_specs`.
    """

    parsed_runs: list[ParsedRun] = []
    for run_spec in run_specs:
        load_count, run_count = expected_phase_counts(run_spec.config_path)
        collection_id = parse_collection_id(run_spec.stdout_log_path)
        runtime_payload = parse_phase_runtimes(
            run_spec.benchmark_log_path,
            expected_load_count=load_count,
            expected_run_count=run_count,
        )
        parsed_runs.append(
            ParsedRun(
                suite_slug=run_spec.suite_slug,
                suite_label=run_spec.suite_label,
                mode=run_spec.mode,
                mode_label=run_spec.mode_label,
                color=run_spec.color,
                config_path=run_spec.config_path,
                collection_id=collection_id,
                total_time_s=read_collection_time_s(collection_id),
                load_sum_s=float(runtime_payload["load_sum_s"]),
                run_sum_s=float(runtime_payload["run_sum_s"]),
                load_count=int(runtime_payload["load_count"]),
                run_count=int(runtime_payload["run_count"]),
                stdout_log_path=run_spec.stdout_log_path,
                benchmark_log_path=run_spec.benchmark_log_path,
            )
        )
    return parsed_runs


def to_metrics_df(parsed_runs: list[ParsedRun]) -> pl.DataFrame:
    """Convert parsed runs into one report dataframe.

    Args:
        parsed_runs: Parsed metrics for all completed runs.

    Returns:
        DataFrame containing one row per run.
    """

    rows = [
        {
            "suite": item.suite_slug,
            "suite_label": item.suite_label,
            "mode": item.mode,
            "collection_id": item.collection_id,
            "config_path": item.config_path.as_posix(),
            "total_time_s": item.total_time_s,
            "load_sum_s": item.load_sum_s,
            "run_sum_s": item.run_sum_s,
            "load_count": item.load_count,
            "run_count": item.run_count,
            "stdout_log": item.stdout_log_path.relative_to(ROOT).as_posix(),
            "benchmark_log": item.benchmark_log_path.relative_to(ROOT).as_posix(),
        }
        for item in parsed_runs
    ]
    return pl.DataFrame(rows)


def pairwise_suite_deltas(metrics_df: pl.DataFrame) -> pl.DataFrame:
    """Build pairwise THP-mode deltas inside each suite.

    Args:
        metrics_df: Per-run metrics dataframe.

    Returns:
        DataFrame containing absolute and percent deltas for each pair.
    """

    comparisons = [
        ("always", "madvise"),
        ("always", "never"),
        ("madvise", "never"),
    ]
    rows: list[dict[str, Any]] = []
    for suite_slug in metrics_df["suite"].unique().to_list():
        suite_rows = metrics_df.filter(pl.col("suite") == suite_slug)
        for lhs_mode, rhs_mode in comparisons:
            lhs = suite_rows.filter(pl.col("mode") == lhs_mode).row(0, named=True)
            rhs = suite_rows.filter(pl.col("mode") == rhs_mode).row(0, named=True)
            for metric in ("total_time_s", "load_sum_s", "run_sum_s"):
                rhs_value = float(rhs[metric])
                delta_s = float(lhs[metric]) - rhs_value
                delta_pct = (
                    (delta_s / rhs_value) * 100.0 if not math.isclose(rhs_value, 0.0) else math.nan
                )
                rows.append(
                    {
                        "suite": suite_slug,
                        "comparison": f"{lhs_mode} - {rhs_mode}",
                        "metric": metric,
                        "lhs_value_s": float(lhs[metric]),
                        "rhs_value_s": rhs_value,
                        "delta_s": delta_s,
                        "delta_pct": delta_pct,
                    }
                )
    return pl.DataFrame(rows)


def cross_suite_deltas(metrics_df: pl.DataFrame) -> pl.DataFrame:
    """Build same-mode deltas between the two new suites.

    Args:
        metrics_df: Per-run metrics dataframe.

    Returns:
        DataFrame comparing the 50/50 suite against the baseline outer10 suite.
    """

    base_suite = "outer10"
    compare_suite = "outer10_update50_delete50"
    rows: list[dict[str, Any]] = []
    for mode in ("always", "madvise", "never"):
        base_row = metrics_df.filter((pl.col("suite") == base_suite) & (pl.col("mode") == mode)).row(
            0, named=True
        )
        compare_row = metrics_df.filter(
            (pl.col("suite") == compare_suite) & (pl.col("mode") == mode)
        ).row(0, named=True)
        for metric in ("total_time_s", "load_sum_s", "run_sum_s"):
            base_value = float(base_row[metric])
            compare_value = float(compare_row[metric])
            delta_s = compare_value - base_value
            delta_pct = (
                (delta_s / base_value) * 100.0 if not math.isclose(base_value, 0.0) else math.nan
            )
            rows.append(
                {
                    "mode": mode,
                    "comparison": f"{compare_suite} - {base_suite}",
                    "metric": metric,
                    "base_value_s": base_value,
                    "compare_value_s": compare_value,
                    "delta_s": delta_s,
                    "delta_pct": delta_pct,
                }
            )
    return pl.DataFrame(rows)


def build_chart(metrics_df: pl.DataFrame, *, suite_slug: str, output_path: Path) -> None:
    """Render one grouped bar chart for the requested suite.

    Args:
        metrics_df: Per-run metrics dataframe.
        suite_slug: Stable suite identifier to graph.
        output_path: Target PNG path.
    """

    suite_rows = metrics_df.filter(pl.col("suite") == suite_slug).sort("mode")
    categories = ["Total time", "Load sum", "Run sum"]
    series = []
    for row in suite_rows.iter_rows(named=True):
        series.append(
            {
                "label": f"THP {row['mode']}",
                "color": next(spec.color for spec in RUN_SPECS if spec.mode == row["mode"]),
                "means": [
                    float(row["total_time_s"]),
                    float(row["load_sum_s"]),
                    float(row["run_sum_s"]),
                ],
                "stds": [0.0, 0.0, 0.0],
            }
        )
    grouped_bar_png(
        categories=categories,
        series=series,
        title=suite_rows["suite_label"][0],
        ylabel="Seconds",
        output_path=output_path,
    )


def _format_value(value: Any) -> str:
    """Format one scalar for markdown tables.

    Args:
        value: Scalar value to render.

    Returns:
        Human-readable string suitable for markdown.
    """

    if isinstance(value, float):
        if math.isfinite(value):
            return f"{value:.3f}"
        return "n/a"
    return str(value)


def markdown_table(df: pl.DataFrame) -> str:
    """Render one dataframe as a markdown table.

    Args:
        df: Table to render.

    Returns:
        Markdown table string.
    """

    headers = df.columns
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in df.iter_rows(named=True):
        lines.append("| " + " | ".join(_format_value(row[name]) for name in headers) + " |")
    return "\n".join(lines)


def read_monitor_entries() -> list[str]:
    """Return logged 20-minute monitoring entries.

    Returns:
        Timestamped monitor status lines from the monitor log, or an empty list.
    """

    if not MONITOR_LOG_PATH.is_file():
        return []
    entries = []
    for line in MONITOR_LOG_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("[") and "status=" in line:
            entries.append(line)
    return entries


def write_results_markdown(
    *,
    container_name: str,
    metrics_df: pl.DataFrame,
    pairwise_df: pl.DataFrame,
    cross_suite_df: pl.DataFrame,
    monitor_entries: list[str],
    commands: list[str],
) -> None:
    """Write the markdown report requested by the user.

    Args:
        container_name: Docker container used for the run.
        metrics_df: Per-run metrics.
        pairwise_df: Within-suite deltas.
        cross_suite_df: Cross-suite deltas.
        monitor_entries: Logged monitoring checks.
        commands: Exact commands launched during collection.
    """

    lines = [
        "# Redis THP Runtime Comparison",
        "",
        f"Generated at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        f"- Container: `{container_name}`",
        "- Session: `redis-thp-compare` on the host, because `tmux` is not installed in the container",
        "",
        "## Commands",
        "",
        "```bash",
        *commands,
        "```",
        "",
        "## Per-Run Metrics",
        "",
        markdown_table(metrics_df.select(
            "suite",
            "mode",
            "collection_id",
            "total_time_s",
            "load_sum_s",
            "run_sum_s",
            "load_count",
            "run_count",
        )),
        "",
        "## Pairwise Suite Deltas",
        "",
        markdown_table(pairwise_df),
        "",
        "## Cross-Suite Deltas",
        "",
        markdown_table(cross_suite_df),
        "",
        "## Preserved Logs",
        "",
        markdown_table(metrics_df.select("suite", "mode", "stdout_log", "benchmark_log")),
        "",
        "## Monitoring Checks",
        "",
    ]
    if monitor_entries:
        lines.extend(f"- `{entry}`" for entry in monitor_entries)
    else:
        lines.append("- No monitor log found.")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- [outer10 chart]({OUTER10_PNG_PATH.name})",
            f"- [outer10 update/delete 50/50 chart]({UPDATE50_PNG_PATH.name})",
            f"- [HTML dashboard]({DASHBOARD_PATH.name})",
            "",
        ]
    )
    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")


def write_dashboard(
    *,
    metrics_df: pl.DataFrame,
    pairwise_df: pl.DataFrame,
    cross_suite_df: pl.DataFrame,
    container_name: str,
    monitor_entries: list[str],
) -> None:
    """Write the standalone HTML dashboard.

    Args:
        metrics_df: Per-run metrics dataframe.
        pairwise_df: Within-suite deltas.
        cross_suite_df: Cross-suite deltas.
        container_name: Docker container used for the run.
        monitor_entries: Logged monitoring checks.
    """

    sections = [
        {
            "title": "Outer Repeat 10",
            "description": "Total, load, and run time across the matched read/delete suite.",
            "image_path": OUTER10_PNG_PATH,
            "table": metrics_df.filter(pl.col("suite") == "outer10").select(
                "mode",
                "collection_id",
                "total_time_s",
                "load_sum_s",
                "run_sum_s",
                "load_count",
                "run_count",
            ),
        },
        {
            "title": "Outer Repeat 10 Update/Delete 50/50",
            "description": "Total, load, and run time across the matched 50/50 update/delete suite.",
            "image_path": UPDATE50_PNG_PATH,
            "table": metrics_df.filter(pl.col("suite") == "outer10_update50_delete50").select(
                "mode",
                "collection_id",
                "total_time_s",
                "load_sum_s",
                "run_sum_s",
                "load_count",
                "run_count",
            ),
        },
        {
            "title": "Pairwise Deltas",
            "description": "THP mode deltas inside each suite.",
            "table": pairwise_df,
        },
        {
            "title": "Cross-Suite Deltas",
            "description": "How each THP mode changed when the workload switched to update/delete 50/50.",
            "table": cross_suite_df,
            "bullets": [
                f"Container: {container_name}",
                "Monitoring checks: " + (", ".join(monitor_entries) if monitor_entries else "none recorded"),
            ],
        },
    ]
    write_html_report(
        title="Redis THP Runtime Comparison",
        subtitle="Two matched outer_repeat=10 suites with preserved collect and benchmark logs.",
        sections=sections,
        output_path=DASHBOARD_PATH,
    )


def main() -> None:
    """Validate configs and generate Redis THP comparison artifacts."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-configs-only",
        action="store_true",
        help="Validate the new config family and exit without reading run artifacts.",
    )
    args = parser.parse_args()

    validate_config_suites(RUN_SPECS)
    if args.verify_configs_only:
        return

    parsed_runs = collect_run_metrics(RUN_SPECS)
    metrics_df = to_metrics_df(parsed_runs)
    pairwise_df = pairwise_suite_deltas(metrics_df)
    cross_suite_df = cross_suite_deltas(metrics_df)
    build_chart(metrics_df, suite_slug="outer10", output_path=OUTER10_PNG_PATH)
    build_chart(
        metrics_df,
        suite_slug="outer10_update50_delete50",
        output_path=UPDATE50_PNG_PATH,
    )
    container_name = CONTAINER_FILE.read_text(encoding="utf-8").strip() if CONTAINER_FILE.is_file() else "unknown"
    commands = (
        [line for line in COMMANDS_FILE.read_text(encoding="utf-8").splitlines() if line]
        if COMMANDS_FILE.is_file()
        else [run_spec.command for run_spec in RUN_SPECS]
    )
    monitor_entries = read_monitor_entries()
    write_results_markdown(
        container_name=container_name,
        metrics_df=metrics_df,
        pairwise_df=pairwise_df,
        cross_suite_df=cross_suite_df,
        monitor_entries=monitor_entries,
        commands=commands,
    )
    write_dashboard(
        metrics_df=metrics_df,
        pairwise_df=pairwise_df,
        cross_suite_df=cross_suite_df,
        container_name=container_name,
        monitor_entries=monitor_entries,
    )


if __name__ == "__main__":
    main()

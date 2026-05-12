from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path("/users/SahilSC/HugePageResearch-gups-harness")
LABEL = "gups_chunked_10pages_20260501T012721Z"
BASE_DIR = ROOT / "temp_data_analysis" / LABEL
RAW_DIR = BASE_DIR / "raw"
BENCHMARK_DIR = Path("/users/SahilSC/kernmlops-benchmark")
GUPS_BINARY = BENCHMARK_DIR / "gups" / "gups"
TARGET_BASE_RUNTIME_S = 60.0
PILOT_CANDIDATES = [
    {"size_gib": 2, "updates_multiplier": 4},
    {"size_gib": 2, "updates_multiplier": 5},
    {"size_gib": 2, "updates_multiplier": 6},
]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


replay_gups = load_module(
    "replay_gups_chunked",
    ROOT / "python" / "kernmlops" / "replay" / "replay_gups.py",
)
generate_gups_breakpoints = load_module(
    "generate_gups_breakpoints_chunked",
    ROOT / "python" / "kernmlops" / "replay" / "generate_gups_breakpoints.py",
)
render_gups_split_harness = load_module(
    "render_gups_split_harness_chunked",
    ROOT / "temp_data_analysis" / "render_gups_split_harness.py",
)


def write_base_only_breakpoints(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [
            {
                "row_kind": "base_pages",
                "row_label": "base_pages",
                "target_page_index": -1,
                "target_column": "",
                "target_update_count": 0,
                "target_break_after_page_accesses": -1,
                "target_page_indices": "",
                "target_page_count": 0,
                "page_group": "",
            }
        ]
    ).write_parquet(path)


def read_single_runtime(results_path: Path) -> float:
    df = pl.read_parquet(results_path)
    if df.height != 1:
        raise RuntimeError(f"Expected one pilot row in {results_path}, got {df.height}.")
    return float(df[0, "runtime_s_1"])


def run_pilot_candidate(
    *,
    size_gib: int,
    updates_multiplier: int,
    breakpoints_path: Path,
) -> float:
    output_path = RAW_DIR / (
        f"pilot_{size_gib}gib_u{updates_multiplier}_results.parquet"
    )
    artifacts_dir = RAW_DIR / f"pilot_{size_gib}gib_u{updates_multiplier}_artifacts"
    metadata_path = replay_gups.run_benchmark(
        breakpoints_df=pl.read_parquet(breakpoints_path),
        output=output_path,
        artifacts_dir=artifacts_dir,
        benchmark_dir=BENCHMARK_DIR,
        table_size_gib=size_gib,
        table_size_mib=None,
        repeats=1,
        updates_multiplier=updates_multiplier,
        stream_seed=7,
        runs=1,
        collectors=(),
        split_mode="pre_split",
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["breakpoints_path"] = str(breakpoints_path)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return read_single_runtime(output_path)


def choose_candidate(pilot_results: list[dict[str, float]]) -> dict[str, float]:
    return min(
        pilot_results,
        key=lambda row: (
            abs(row["runtime_s"] - TARGET_BASE_RUNTIME_S),
            row["size_gib"],
            row["updates_multiplier"],
        ),
    )


def run_calibration(size_gib: int, updates_multiplier: int) -> dict[str, str]:
    results_path = RAW_DIR / "calibration_results.jsonl"
    page_summary_path = RAW_DIR / "calibration_page_summary.csv"
    stdout_path = RAW_DIR / "calibration_stdout.log"
    command = [
        str(GUPS_BINARY),
        "--results",
        str(results_path),
        "--table-size-gib",
        str(size_gib),
        "--repeats",
        "1",
        "--updates-multiplier",
        str(updates_multiplier),
        "--threads",
        "1",
        "--stream-seed",
        "7",
        "--page-summary-out",
        str(page_summary_path),
    ]

    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "1"
    system_config = replay_gups.setup_system()
    try:
        replay_gups._set_thp_enabled_mode("always")
        with stdout_path.open("w", encoding="utf-8") as stdout_file:
            subprocess.check_call(command, stdout=stdout_file, stderr=stdout_file, env=env)
    finally:
        replay_gups.teardown_system(system_config)

    return {
        "command": shlex.join(command),
        "results_path": str(results_path),
        "page_summary_path": str(page_summary_path),
        "stdout_path": str(stdout_path),
    }


def write_chunk_breakpoints(page_summary_path: Path) -> Path:
    output_path = RAW_DIR / "breakpoints.parquet"
    summaries = generate_gups_breakpoints.load_page_summaries(page_summary_path)
    rows = generate_gups_breakpoints.generate_breakpoint_rows(
        summaries,
        hot_page_chunks=((1, 10), (11, 20), (21, 30)),
        least_hot_page_chunks=((1, 10),),
    )
    expected_labels = [
        "base_pages",
        "no_break",
        "hot pages 1 to 10",
        "hot pages 11 to 20",
        "hot pages 21 to 30",
        "least hot pages 1 to 10",
    ]
    labels = [str(row["row_label"]) for row in rows]
    if labels != expected_labels:
        raise RuntimeError(f"Unexpected breakpoint labels: {labels}")
    generate_gups_breakpoints.write_breakpoints(rows, output_path)
    return output_path


def run_final_matrix(
    *,
    size_gib: int,
    updates_multiplier: int,
    breakpoints_path: Path,
) -> Path:
    output_path = RAW_DIR / "gups_split_results.parquet"
    metadata_path = replay_gups.run_benchmark(
        breakpoints_df=pl.read_parquet(breakpoints_path),
        output=output_path,
        artifacts_dir=RAW_DIR / "gups_split_artifacts",
        benchmark_dir=BENCHMARK_DIR,
        table_size_gib=size_gib,
        table_size_mib=None,
        repeats=1,
        updates_multiplier=updates_multiplier,
        stream_seed=7,
        runs=3,
        collectors=(),
        split_mode="pre_split",
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["breakpoints_path"] = str(breakpoints_path)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata_path


def render_dashboard(results_path: Path, metadata_path: Path) -> dict[str, str]:
    outputs = render_gups_split_harness.render_dashboard(
        results_path=results_path,
        metadata_path=metadata_path,
        output_dir=BASE_DIR,
        label=LABEL,
    )
    return {key: str(value) for key, value in outputs.items() if key != "count_curve_pngs"} | {
        "count_curve_pngs": [str(path) for path in outputs["count_curve_pngs"]]
    }


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()

    pilot_breakpoints_path = RAW_DIR / "pilot_base_only_breakpoints.parquet"
    write_base_only_breakpoints(pilot_breakpoints_path)

    pilot_results: list[dict[str, float]] = []
    for candidate in PILOT_CANDIDATES:
        size_gib = int(candidate["size_gib"])
        updates_multiplier = int(candidate["updates_multiplier"])
        runtime_s = run_pilot_candidate(
            size_gib=size_gib,
            updates_multiplier=updates_multiplier,
            breakpoints_path=pilot_breakpoints_path,
        )
        pilot_results.append(
            {
                "size_gib": float(size_gib),
                "updates_multiplier": float(updates_multiplier),
                "runtime_s": runtime_s,
            }
        )
        print(
            f"pilot size={size_gib} GiB updates_multiplier={updates_multiplier} "
            f"runtime_s={runtime_s:.6f}",
            flush=True,
        )

    chosen = choose_candidate(pilot_results)
    chosen_size_gib = int(chosen["size_gib"])
    chosen_updates_multiplier = int(chosen["updates_multiplier"])
    print(
        f"chosen size={chosen_size_gib} GiB "
        f"updates_multiplier={chosen_updates_multiplier} "
        f"base_runtime_s={chosen['runtime_s']:.6f}",
        flush=True,
    )

    calibration = run_calibration(chosen_size_gib, chosen_updates_multiplier)
    breakpoints_path = write_chunk_breakpoints(Path(calibration["page_summary_path"]))
    metadata_path = run_final_matrix(
        size_gib=chosen_size_gib,
        updates_multiplier=chosen_updates_multiplier,
        breakpoints_path=breakpoints_path,
    )
    results_path = RAW_DIR / "gups_split_results.parquet"
    dashboard_outputs = render_dashboard(results_path, metadata_path)

    final_df = pl.read_parquet(results_path)
    final_rows = []
    for row in final_df.iter_rows(named=True):
        runtimes = [
            float(row[f"runtime_s_{run_number}"])
            for run_number in range(1, 4)
        ]
        successes = [
            int(row[f"split_successes_{run_number}"])
            for run_number in range(1, 4)
        ]
        final_rows.append(
            {
                "row_label": row["row_label"],
                "row_kind": row["row_kind"],
                "target_page_count": int(row.get("target_page_count") or 0),
                "runtime_s_values": runtimes,
                "runtime_s_mean": sum(runtimes) / len(runtimes),
                "split_successes_total": sum(successes),
            }
        )

    summary = {
        "label": LABEL,
        "target_base_runtime_s": TARGET_BASE_RUNTIME_S,
        "pilot_results": pilot_results,
        "chosen": chosen,
        "chosen_size_gib": chosen_size_gib,
        "chosen_updates_multiplier": chosen_updates_multiplier,
        "calibration": calibration,
        "breakpoints_path": str(breakpoints_path),
        "results_path": str(results_path),
        "metadata_path": str(metadata_path),
        "dashboard_dir": str(BASE_DIR),
        "dashboard_outputs": dashboard_outputs,
        "commands_log_path": str(RAW_DIR / "gups_split_artifacts" / "run_commands.log"),
        "final_rows": final_rows,
        "total_wall_s": time.monotonic() - started_at,
    }
    (RAW_DIR / "experiment_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()

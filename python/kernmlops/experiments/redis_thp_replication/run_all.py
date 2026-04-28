"""Run the overnight THP replication, dispatch, and pinning experiments."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _PACKAGE_ROOT = Path(__file__).resolve().parents[3]
    _PACKAGE_ROOT_STR = str(_PACKAGE_ROOT)
    if _PACKAGE_ROOT_STR not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT_STR)

from experiments.redis_thp_replication.common import (
    EXPERIMENT_DIR,
    ensure_experiment_directories,
    now_text,
    write_json,
    write_markdown,
)
from experiments.redis_thp_replication.part_a import PART_A_MANIFEST_PATH, run_part_a
from experiments.redis_thp_replication.part_b import PART_B_MANIFEST_PATH, run_part_b
from experiments.redis_thp_replication.part_c import PART_C_MANIFEST_PATH, run_part_c


TOP_LEVEL_MANIFEST_PATH = EXPERIMENT_DIR / "overnight_changes_manifest.json"
TOP_LEVEL_REPORT_PATH = (
    Path(__file__).resolve().parents[4]
    / "reports"
    / "collector"
    / "redis_thp_replication_2026-04-10.md"
)


def _report_text(
    *,
    started_at_utc: str,
    finished_at_utc: str,
    container_name: str,
    manifests: dict[str, dict[str, Any]],
) -> str:
    """Build the final markdown report for the overnight-change run."""

    part_a = manifests["part_a"]
    part_b = manifests["part_b"]
    part_c = manifests["part_c"]
    chosen = part_a["recommended_capture_config"]

    commands = [
        (
            "Create the branch with the current dirty tree",
            "Run this from the host checkout before making changes so the overnight work stays off main while preserving both tracked and untracked files.",
            "git switch -c overnight-changes",
        ),
        (
            "Run the full overnight experiment inside the existing Docker container",
            "This starts part A, then the mixed-dispatch replay, then the pinning replay. It is the shortest path to reproduce the full set of artifacts.",
            (
                "sudo docker exec -it "
                f"{container_name} bash -lc 'cd /KernMLOps && "
                ".venv/bin/python python/kernmlops/experiments/redis_thp_replication/run_all.py "
                f"--container-name {container_name}'"
            ),
        ),
        (
            "Run only part A",
            "Use this if you want to repeat the raw Redis/YCSB THP comparison and the bounded 1.5-hour config search without replay work.",
            (
                "sudo docker exec -it "
                f"{container_name} bash -lc 'cd /KernMLOps && "
                ".venv/bin/python python/kernmlops/experiments/redis_thp_replication/part_a.py'"
            ),
        ),
        (
            "Run only part B",
            "Use this after part A has written its manifest. The script reads the chosen or fallback config from part A automatically.",
            (
                "sudo docker exec -it "
                f"{container_name} bash -lc 'cd /KernMLOps && "
                ".venv/bin/python python/kernmlops/experiments/redis_thp_replication/part_b.py "
                f"--part-a-manifest {PART_A_MANIFEST_PATH}'"
            ),
        ),
        (
            "Run only part C",
            "Use this after part A has written its manifest. The script keeps khugepaged enabled at 500 ms and restores Redis affinity after the run.",
            (
                "sudo docker exec -it "
                f"{container_name} bash -lc 'cd /KernMLOps && "
                ".venv/bin/python python/kernmlops/experiments/redis_thp_replication/part_c.py "
                f"--part-a-manifest {PART_A_MANIFEST_PATH}'"
            ),
        ),
    ]

    lines = [
        "# Redis THP Replication, Dispatch, and Pinning",
        "",
        "## Overview",
        "",
        f"- started: `{started_at_utc}`",
        f"- finished: `{finished_at_utc}`",
        f"- container: `{container_name}`",
        "- all experiments are runtime-only and collect no dTLB or other hardware counters",
        "",
        "## High-Level Changes",
        "",
        "- Part A now lives in a one-off experiment package that measures raw YCSB load time, run time, and load+run time directly inside Docker.",
        "- Part B adds a durable `--break-dispatch` replay option so replay can break pages inline or through a background worker thread.",
        "- Part C uses a one-off replay wrapper to compare Redis unpinned versus pinned while keeping khugepaged enabled at 500 ms.",
        "",
        "## Architecture",
        "",
        "- The one-off package is under `python/kernmlops/experiments/redis_thp_replication/` so the normal benchmark harness stays mostly untouched.",
        "- Part A and Part C use one-off wrappers because they need experiment-specific environment control and visuals that do not belong in the shared harness.",
        "- Part B changes `python/kernmlops/replay/replay_trace.py` because break dispatch is a true replay behavior difference, not just an outside orchestration detail.",
        "",
        "## Design Decisions",
        "",
        "- The screenshot replication uses symmetric manifests for `always`, `madvise`, and `never` so the old asymmetric checked-in configs do not distort the comparison.",
        "- The part A search is capped at 1.5 hours so the tuning loop cannot consume the whole night while chasing a better THP-sensitive config.",
        "- Part C restores Redis affinity to the all-CPU mask after the experiment so the pinning test does not leak into later work.",
        "",
        "## Part A",
        "",
        f"- clear THP effect found: `{part_a['clear_thp_effect_found']}`",
        f"- chosen stage: `{part_a['chosen_stage']}`",
        f"- chosen config name: `{part_a['chosen_config_name']}`",
        f"- recommended replay config: `{chosen['name']}` with record_count=`{chosen['record_count']}` and operation_count=`{chosen['operation_count']}`",
        f"- html: `{part_a['artifacts']['html']}`",
        f"- main png: `{part_a['artifacts']['runtime_png']}`",
        f"- supplemental png: `{part_a['artifacts']['supplemental_png']}`",
        "",
        "Configs explored in the 1.5-hour search window are listed in the part A manifest attempt log. The runner stops early if the clear THP-effect rule is met.",
        "",
        "## Part B",
        "",
        "- `--break-dispatch mixed` means runs 1-4 are inline and runs 5-8 are threaded.",
        f"- html: `{part_b['artifacts']['html']}`",
        f"- main png: `{part_b['artifacts']['runtime_png']}`",
        f"- split png: `{part_b['artifacts']['split_png']}`",
        "",
        "This part compares total replay runtime as well as replay-time split wall cost for the same breakpoint rows.",
        "",
        "## Part C",
        "",
        "- This part frames pinning as a scheduler-consistency question rather than the main expected source of speedup.",
        f"- html: `{part_c['artifacts']['html']}`",
        f"- main png: `{part_c['artifacts']['runtime_png']}`",
        f"- split png: `{part_c['artifacts']['split_png']}`",
        "",
        "The pinning wrapper keeps khugepaged enabled at 500 ms and restores the normal all-CPU Redis affinity afterwards.",
        "",
        "## Reproduction Commands",
        "",
    ]
    for title, description, command in commands:
        lines.extend(
            [
                f"### {title}",
                "",
                description,
                "",
                f"`{command}`",
                "",
            ]
        )
    return "\n".join(lines)


def run_all(*, container_name: str) -> dict[str, Any]:
    """Run part A, part B, and part C sequentially and write the final report."""

    ensure_experiment_directories()
    started_at_utc = now_text()
    part_a_manifest = run_part_a()
    part_b_manifest = run_part_b(PART_A_MANIFEST_PATH)
    part_c_manifest = run_part_c(PART_A_MANIFEST_PATH)
    finished_at_utc = now_text()
    manifest = {
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "container_name": container_name,
        "part_a": part_a_manifest,
        "part_b": part_b_manifest,
        "part_c": part_c_manifest,
    }
    write_json(TOP_LEVEL_MANIFEST_PATH, manifest)
    TOP_LEVEL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_markdown(
        TOP_LEVEL_REPORT_PATH,
        _report_text(
            started_at_utc=started_at_utc,
            finished_at_utc=finished_at_utc,
            container_name=container_name,
            manifests=manifest,
        ),
    )
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--container-name",
        default=os.environ.get("CONTAINER_NAME", os.uname().nodename),
        help="Human-readable Docker container name used in the report.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the whole overnight change set from the command line."""

    args = _build_parser().parse_args(argv)
    manifest = run_all(container_name=args.container_name)
    print(manifest["finished_at_utc"])


if __name__ == "__main__":
    main()

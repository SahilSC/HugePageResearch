# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from experiments.redis_thp_replication.runtime_search import (
    SearchSettings,
    _initial_manifest,
    _load_resume_manifest,
    _parse_last_runtime_s_or_none,
    _timed_run_command_with_timeout,
    build_stage1_seed_candidates,
    build_stage2_candidates,
    is_clear_thp_effect,
    rank_attempt_summaries,
    should_pivot_stage,
)


class RedisRuntimeSearchTests(unittest.TestCase):
    def test_stage1_candidates_keep_delete_floor_and_sum_to_one(self) -> None:
        candidates = build_stage1_seed_candidates()

        self.assertEqual(len(candidates), 24)
        self.assertEqual(len({candidate.name for candidate in candidates}), 24)

        for candidate in candidates:
            measured = candidate.measured
            total = (
                measured.read_proportion
                + measured.update_proportion
                + measured.delete_proportion
                + measured.insert_proportion
                + measured.readmodifywrite_proportion
                + measured.scan_proportion
            )
            self.assertGreaterEqual(measured.delete_proportion, 0.05)
            self.assertAlmostEqual(total, 1.0)
            self.assertEqual(measured.outer_repeat, 1)

    def test_stage2_candidates_tag_warmup_and_helper_variants(self) -> None:
        seed = build_stage1_seed_candidates()[0]
        stage2_candidates = build_stage2_candidates([seed])

        self.assertEqual(len(stage2_candidates), 4)
        self.assertEqual(
            {candidate.stage for candidate in stage2_candidates},
            {"stage2_warmup", "stage2_helper"},
        )
        self.assertEqual(
            sum(candidate.helper_pressure is None for candidate in stage2_candidates),
            2,
        )
        self.assertEqual(
            sum(candidate.helper_pressure is not None for candidate in stage2_candidates),
            2,
        )
        for candidate in stage2_candidates:
            self.assertIsNotNone(candidate.warmup)
            warmup = candidate.warmup
            assert warmup is not None
            total = (
                warmup.read_proportion
                + warmup.update_proportion
                + warmup.delete_proportion
                + warmup.insert_proportion
                + warmup.readmodifywrite_proportion
                + warmup.scan_proportion
            )
            self.assertAlmostEqual(total, 1.0)

    def test_is_clear_thp_effect_requires_threshold_and_std_guard(self) -> None:
        self.assertTrue(
            is_clear_thp_effect(
                always_mean_s=90.0,
                never_mean_s=100.0,
                always_std_s=1.0,
                never_std_s=2.0,
            )
        )
        self.assertFalse(
            is_clear_thp_effect(
                always_mean_s=96.0,
                never_mean_s=100.0,
                always_std_s=1.0,
                never_std_s=2.0,
            )
        )
        self.assertFalse(
            is_clear_thp_effect(
                always_mean_s=90.0,
                never_mean_s=100.0,
                always_std_s=12.0,
                never_std_s=1.0,
            )
        )

    def test_rank_attempt_summaries_prefers_stronger_delta_then_lower_noise(self) -> None:
        rows = [
            {
                "candidate_name": "close",
                "delta_pct": 0.049,
                "delta_s": 4.9,
                "combined_std_s": 0.1,
                "eval_total_mean_s": 100.0,
            },
            {
                "candidate_name": "best",
                "delta_pct": 0.07,
                "delta_s": 7.0,
                "combined_std_s": 0.3,
                "eval_total_mean_s": 120.0,
            },
            {
                "candidate_name": "same_delta_less_noise",
                "delta_pct": 0.07,
                "delta_s": 7.0,
                "combined_std_s": 0.1,
                "eval_total_mean_s": 120.0,
            },
        ]

        ranked = rank_attempt_summaries(rows)

        self.assertEqual(ranked[0]["candidate_name"], "same_delta_less_noise")
        self.assertEqual(ranked[1]["candidate_name"], "best")
        self.assertEqual(ranked[2]["candidate_name"], "close")

    def test_should_pivot_stage_after_deadline_only_for_stage1(self) -> None:
        self.assertFalse(
            should_pivot_stage(
                started_epoch_s=0.0,
                now_epoch_s=100.0,
                stage1_hours=4.5,
                current_stage="stage1_seed",
            )
        )
        self.assertTrue(
            should_pivot_stage(
                started_epoch_s=0.0,
                now_epoch_s=(4.5 * 3600.0) + 1.0,
                stage1_hours=4.5,
                current_stage="stage1_seed",
            )
        )
        self.assertFalse(
            should_pivot_stage(
                started_epoch_s=0.0,
                now_epoch_s=(4.5 * 3600.0) + 1.0,
                stage1_hours=4.5,
                current_stage="stage2_warmup",
            )
        )

    def test_timed_run_command_with_timeout_marks_timeout(self) -> None:
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "timeout.log"
            elapsed_s, timed_out = _timed_run_command_with_timeout(
                [sys.executable, "-c", "import time; time.sleep(1.0)"],
                log_path=log_path,
                timeout_seconds=0.1,
            )

            self.assertTrue(timed_out)
            self.assertGreaterEqual(elapsed_s, 0.1)
            self.assertIn("Timed out after", log_path.read_text(encoding="utf-8"))

    def test_manifest_roundtrip_is_resume_safe(self) -> None:
        with TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir) / "artifacts"
            artifact_root.mkdir()
            settings = SearchSettings(
                container_name="heuristic_allen",
                branch_name="redis_runtimes",
                total_budget_hours=8.0,
                stage1_hours=4.5,
                max_mode_runtime_minutes=30.0,
            )
            manifest = _initial_manifest(settings=settings, artifact_root=artifact_root)
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")

            loaded = _load_resume_manifest(manifest_path)

            self.assertEqual(loaded["branch_name"], "redis_runtimes")
            self.assertEqual(loaded["container_name"], "heuristic_allen")
            self.assertEqual(loaded["artifact_root"], str(artifact_root))

    def test_parse_last_runtime_s_or_none_handles_missing_overall(self) -> None:
        with TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "run.log"
            log_path.write_text("Starting test.\nNo final summary here.\n", encoding="utf-8")

            self.assertIsNone(_parse_last_runtime_s_or_none(log_path))


if __name__ == "__main__":
    unittest.main()

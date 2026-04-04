# ruff: noqa: E402
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from cli.collect import _clean_redis_collection, _discover_redis_server_tgids


class CollectCleanHelpersTest(unittest.TestCase):
    def test_discover_redis_server_tgids_filters_process_trace(self):
        df = pl.DataFrame(
            {
                "name": ["redis-server", "redis-server-aux", "python"],
                "tgid": [101, 202, 303],
            }
        )

        self.assertEqual(_discover_redis_server_tgids(df), {101, 202})

    def test_clean_redis_collection_filters_to_single_tgid(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            curated_dir = Path(tmp_dir) / "curated"
            run_dir = curated_dir / "redis" / "run-123"
            run_dir.mkdir(parents=True)

            pl.DataFrame(
                {
                    "name": ["redis-server", "python"],
                    "tgid": [111, 222],
                    "collection_id": ["run-123", "run-123"],
                }
            ).write_parquet(run_dir / "process_trace.0.parquet")

            pl.DataFrame(
                {
                    "tgid": [111, 222],
                    "value": [1, 2],
                    "collection_id": ["run-123", "run-123"],
                }
            ).write_parquet(run_dir / "memory_usage.0.parquet")

            cleaned_collection_id = _clean_redis_collection(
                curated_dir=curated_dir,
                collection_id="run-123",
                verbose=False,
                ids=None,
            )

            self.assertEqual(cleaned_collection_id, "cleanedrun-123")
            cleaned_run_dir = curated_dir / "redis" / "cleanedrun-123"
            memory_usage = pl.read_parquet(cleaned_run_dir / "memory_usage.0.parquet")
            self.assertEqual(memory_usage["tgid"].to_list(), [111])
            self.assertEqual(
                memory_usage["collection_id"].to_list(),
                ["cleanedrun-123"],
            )


if __name__ == "__main__":
    unittest.main()

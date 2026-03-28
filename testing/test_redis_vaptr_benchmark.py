# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from kernmlops_benchmark.redis import RedisBenchmark, RedisConfig


class RedisVAPtrBenchmarkTest(unittest.TestCase):
    def _make_benchmark(self, **config_overrides) -> RedisBenchmark:
        generic_config = mock.Mock()
        generic_config.get_benchmark_dir.return_value = Path("/tmp")
        config = RedisConfig(**config_overrides)
        return RedisBenchmark(generic_config=generic_config, config=config)

    def test_vaptr_key_names_follow_ordered_ycsb_format(self):
        benchmark = self._make_benchmark(
            insert_order="ordered",
            zero_padding=4,
            record_count=3,
            vaptr_num_keys=3,
        )
        self.assertEqual(
            benchmark.vaptr_key_names(),
            ["user0000", "user0001", "user0002"],
        )

    def test_vaptr_key_names_cap_at_record_count(self):
        benchmark = self._make_benchmark(
            insert_order="ordered",
            zero_padding=2,
            record_count=3,
            vaptr_num_keys=10,
        )
        self.assertEqual(benchmark.vaptr_key_names(), ["user00", "user01", "user02"])


if __name__ == "__main__":
    unittest.main()

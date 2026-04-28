# ruff: noqa: E402
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from kernmlops_benchmark.benchmark import GenericBenchmarkConfig
from kernmlops_benchmark.redis import RedisBenchmark, RedisConfig
from redis_runtime import RedisEndpoint


class RedisVAPtrBenchmarkTest(unittest.TestCase):
    def _make_benchmark(self, **config_overrides) -> RedisBenchmark:
        generic_config = GenericBenchmarkConfig(benchmark="redis", benchmark_dir="/tmp")
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

    def test_run_uses_configured_endpoint_and_single_scan_proportion(self):
        benchmark = self._make_benchmark(
            repeat=1,
            outer_repeat=1,
            load_from_rdb=True,
        )
        endpoint = RedisEndpoint(host="10.1.2.3", port=6380)
        server_process = mock.Mock()
        load_process = mock.Mock()
        load_process.wait.return_value = None
        load_process.returncode = 0
        run_process = mock.Mock()

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                mock.patch(
                    "kernmlops_benchmark.redis.load_repo_redis_endpoint",
                    return_value=endpoint,
                ),
                mock.patch(
                    "kernmlops_benchmark.redis.load_repo_redis_cli_path",
                    return_value=Path("/tmp/redis-7.4.2/src/redis-cli"),
                ),
                mock.patch(
                    "kernmlops_benchmark.redis.load_repo_redis_server_path",
                    return_value=Path("/tmp/redis-7.4.2/src/redis-server"),
                ),
                mock.patch(
                    "kernmlops_benchmark.redis.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess(["redis-cli"], 0),
                        subprocess.CompletedProcess(["redis-cli"], 0),
                        subprocess.CompletedProcess(
                            ["redis-cli"],
                            0,
                            stdout="42\n",
                            stderr="",
                        ),
                    ],
                ) as run_mock,
                mock.patch(
                    "kernmlops_benchmark.redis.subprocess.Popen",
                    side_effect=[server_process, load_process, run_process],
                ) as popen_mock,
                mock.patch("kernmlops_benchmark.redis.time.sleep"),
                mock.patch("kernmlops_benchmark.redis.demote", return_value=None),
            ):
                benchmark.run(run_dir=Path(tmp_dir))
                assert benchmark._log_file is not None
                benchmark._log_file.close()

        self.assertEqual(
            run_mock.call_args_list[0].args[0],
            [
                "/tmp/redis-7.4.2/src/redis-cli",
                "-h",
                "10.1.2.3",
                "-p",
                "6380",
                "SHUTDOWN",
                "NOSAVE",
            ],
        )
        self.assertEqual(
            run_mock.call_args_list[1].args[0],
            ["/tmp/redis-7.4.2/src/redis-cli", "-h", "10.1.2.3", "-p", "6380", "ping"],
        )
        self.assertEqual(
            run_mock.call_args_list[2].args[0],
            [
                "/tmp/redis-7.4.2/src/redis-cli",
                "-h",
                "10.1.2.3",
                "-p",
                "6380",
                "DBSIZE",
            ],
        )

        load_command = popen_mock.call_args_list[1].args[0]
        run_command = popen_mock.call_args_list[2].args[0]
        server_command = popen_mock.call_args_list[0].args[0]
        self.assertEqual(server_command[0], "/tmp/redis-7.4.2/src/redis-server")
        self.assertIn("redis.host=10.1.2.3", load_command)
        self.assertIn("redis.port=6380", load_command)
        self.assertIn("redis.host=10.1.2.3", run_command)
        self.assertIn("redis.port=6380", run_command)
        self.assertEqual(
            sum(part.startswith("scanproportion=") for part in run_command),
            1,
        )

    def test_run_logs_full_yaml_config_and_dbsize_before_load_phase(self):
        benchmark = self._make_benchmark(repeat=1, outer_repeat=1, load_from_rdb=True)
        endpoint = RedisEndpoint(host="10.1.2.3", port=6380)
        server_process = mock.Mock()
        load_process = mock.Mock()
        load_process.wait.return_value = None
        load_process.returncode = 0
        run_process = mock.Mock()
        config_text = (
            "benchmark_config:\n"
            "  generic:\n"
            "    benchmark: redis\n"
            "    transparent_hugepages: always\n"
            "  redis:\n"
            "    repeat: 1\n"
            "    outer_repeat: 1\n"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                mock.patch(
                    "kernmlops_benchmark.redis.load_repo_redis_endpoint",
                    return_value=endpoint,
                ),
                mock.patch(
                    "kernmlops_benchmark.redis.load_repo_redis_cli_path",
                    return_value=Path("/tmp/redis-7.4.2/src/redis-cli"),
                ),
                mock.patch(
                    "kernmlops_benchmark.redis.load_repo_redis_server_path",
                    return_value=Path("/tmp/redis-7.4.2/src/redis-server"),
                ),
                mock.patch(
                    "kernmlops_benchmark.redis.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess(["redis-cli"], 0),
                        subprocess.CompletedProcess(["redis-cli"], 0),
                        subprocess.CompletedProcess(
                            ["redis-cli"],
                            0,
                            stdout="42\n",
                            stderr="",
                        ),
                    ],
                ),
                mock.patch(
                    "kernmlops_benchmark.redis.subprocess.Popen",
                    side_effect=[server_process, load_process, run_process],
                ),
                mock.patch("kernmlops_benchmark.redis.time.sleep"),
                mock.patch("kernmlops_benchmark.redis.demote", return_value=None),
            ):
                benchmark.run(run_dir=Path(tmp_dir), config_text=config_text)
                assert benchmark._log_file is not None
                benchmark._log_file.flush()

                contents = (Path(tmp_dir) / "redis_benchmark.log").read_text(
                    encoding="utf-8"
                )
                benchmark._log_file.close()

        self.assertIn("Collection YAML config begin", contents)
        self.assertIn("benchmark_config:", contents)
        self.assertIn("transparent_hugepages: always", contents)
        self.assertIn("outer_repeat: 1", contents)
        self.assertIn(
            "Running command: /tmp/redis-7.4.2/src/redis-cli -h 10.1.2.3 -p 6380 DBSIZE",
            contents,
        )
        self.assertIn("Command stdout: 42", contents)
        self.assertLess(
            contents.index("Collection YAML config begin"),
            contents.index("Load phase out_i=0 starting"),
        )

    def test_poll_logs_final_run_finish_for_last_phase(self):
        benchmark = self._make_benchmark()
        benchmark.process = mock.Mock()
        benchmark.process.poll.return_value = 0
        benchmark.process.returncode = 0
        benchmark.server = mock.Mock()
        benchmark._last_run_start = 100.0
        benchmark._last_run_indices = (9, 0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                mock.patch(
                    "kernmlops_benchmark.redis.subprocess.run",
                    return_value=subprocess.CompletedProcess(["redis-cli"], 0),
                ),
                mock.patch("kernmlops_benchmark.redis.time.time", return_value=112.34),
            ):
                log_path = Path(tmp_dir) / "redis_benchmark.log"
                with log_path.open("w", encoding="utf-8") as log_file:
                    benchmark._log_file = log_file
                    return_code = benchmark.poll()

                contents = log_path.read_text(encoding="utf-8")

        self.assertEqual(return_code, 0)
        self.assertIn("Run phase out_i=9 i=0 finished in 12.34s (exit=0)", contents)
        self.assertIn("Redis benchmark finished at", contents)
        self.assertIsNone(benchmark._last_run_start)
        self.assertIsNone(benchmark._last_run_indices)


if __name__ == "__main__":
    unittest.main()

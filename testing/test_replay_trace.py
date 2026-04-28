# ruff: noqa: E402
from __future__ import annotations

import errno
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from replay import replay_trace
from redis_runtime import RedisEndpoint


class RedisMetadataTest(unittest.TestCase):
    def test_get_redis_pid_returns_process_id(self):
        client = mock.Mock()
        client.info.return_value = {"process_id": 4321}

        self.assertEqual(replay_trace._get_redis_pid(client), 4321)
        client.info.assert_called_once_with("server")

    def test_get_redis_pid_fails_fast_when_missing(self):
        client = mock.Mock()
        client.info.return_value = {}

        with self.assertRaisesRegex(RuntimeError, "process_id"):
            replay_trace._get_redis_pid(client)

    def test_require_root_for_counter_collection_fails_fast_when_not_root(self):
        with mock.patch.object(replay_trace.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(PermissionError, "dtlb_loads, dtlb_misses"):
                replay_trace._require_root_for_counter_collection(
                    ("dtlb_loads", "dtlb_misses")
                )

    def test_load_counter_config_reads_collectors_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "collectors.yaml"
            config_path.write_text(
                "collectors:\n"
                "  - dtlb_loads\n"
                "  - dtlb_misses\n",
                encoding="utf-8",
            )

            collector_names = replay_trace._load_counter_config(config_path)

        self.assertEqual(collector_names, ("dtlb_loads", "dtlb_misses"))



class BreakPageTest(unittest.TestCase):
    def test_resolve_key_vaddr_uses_field0_and_returns_int(self):
        client = mock.Mock()
        client.execute_command.return_value = [["user-proof", "0x2000"]]

        self.assertEqual(replay_trace._resolve_key_vaddr(client, "user-proof"), 0x2000)
        client.execute_command.assert_called_once_with(
            "VAPTR",
            "FIELD",
            "field0",
            "user-proof",
        )

    def test_resolve_key_vaddr_returns_missing_pointer_error_for_nil(self):
        client = mock.Mock()
        client.execute_command.return_value = [["user-proof", "(nil)"]]

        with self.assertRaisesRegex(FileNotFoundError, "did not resolve a direct pointer"):
            replay_trace._resolve_key_vaddr(client, "user-proof")

    def test_resolve_key_vaddr_fails_fast_for_unexpected_reply_shape(self):
        client = mock.Mock()
        client.execute_command.return_value = ["user-proof", "0x2000"]

        with self.assertRaisesRegex(RuntimeError, "unexpected reply"):
            replay_trace._resolve_key_vaddr(client, "user-proof")

    def test_resolve_key_vaddr_fails_fast_for_unexpected_key(self):
        client = mock.Mock()
        client.execute_command.return_value = [["other-key", "0x2000"]]

        with self.assertRaisesRegex(RuntimeError, "returned key"):
            replay_trace._resolve_key_vaddr(client, "user-proof")

    def test_break_page_invokes_split_thp_for_cached_pid(self):
        client = mock.Mock()
        client.execute_command.return_value = [["user-proof", "0x3000"]]

        with mock.patch.object(replay_trace, "_invoke_split_thp_syscall") as invoke:
            result = replay_trace._break_page_for_pid(client, 4321, "user-proof")

        self.assertEqual(result.vaddr, 0x3000)
        self.assertEqual(result.attempts, 1)
        self.assertIsNone(result.error)
        invoke.assert_called_once_with(4321, 0x3000)

    def test_break_page_returns_counted_failure_for_missing_pointer(self):
        client = mock.Mock()
        client.execute_command.return_value = [["user-proof", "(nil)"]]

        result = replay_trace._break_page_for_pid(client, 4321, "user-proof")

        self.assertEqual(result.vaddr, 0)
        self.assertEqual(result.attempts, 0)
        self.assertIsInstance(result.error, FileNotFoundError)

    def test_break_page_requires_an_explicit_pid(self):
        client = mock.Mock()
        with mock.patch.object(
            replay_trace,
            "_break_page_for_pid",
            return_value=replay_trace.SplitPageResult(
                vaddr=0x3000,
                attempts=1,
                error=None,
            ),
        ) as break_for_pid:
            self.assertTrue(replay_trace.break_page(client, 4321, "user-proof"))

        break_for_pid.assert_called_once_with(client, 4321, "user-proof")

    def test_break_page_retries_the_syscall_and_returns_false_after_failure(self):
        client = mock.Mock()
        client.execute_command.return_value = [["user-proof", "0x3000"]]

        with mock.patch.object(
            replay_trace,
            "_invoke_split_thp_syscall",
            side_effect=OSError(errno.ENOENT, "No such file or directory"),
        ) as invoke:
            with self.assertLogs(replay_trace.logger, level="WARNING") as logs:
                self.assertFalse(replay_trace.break_page(client, 4321, "user-proof"))

        self.assertEqual(invoke.call_count, replay_trace.BREAK_PAGE_MAX_ATTEMPTS)
        self.assertIn("after 2 attempts", "\n".join(logs.output))


class ReplayInvocationTest(unittest.TestCase):
    def test_wait_for_redis_exit_reaps_a_replay_started_child(self):
        with (
            mock.patch.object(
                replay_trace.os,
                "waitpid",
                return_value=(4321, 0),
            ) as waitpid_mock,
            mock.patch.object(replay_trace.os, "kill") as kill_mock,
        ):
            replay_trace._wait_for_redis_exit(4321, timeout=0.5)

        waitpid_mock.assert_called_once_with(4321, replay_trace.os.WNOHANG)
        kill_mock.assert_not_called()

    def test_wait_for_redis_exit_falls_back_to_process_lookup_for_host_redis(self):
        with (
            mock.patch.object(
                replay_trace.os,
                "waitpid",
                side_effect=ChildProcessError,
            ) as waitpid_mock,
            mock.patch.object(
                replay_trace.os,
                "kill",
                side_effect=ProcessLookupError,
            ) as kill_mock,
        ):
            replay_trace._wait_for_redis_exit(4321, timeout=0.5)

        waitpid_mock.assert_called_once_with(4321, replay_trace.os.WNOHANG)
        kill_mock.assert_called_once_with(4321, 0)

    def test_invoke_break_page_logs_and_continues_after_syscall_failure(self):
        with mock.patch.object(
            replay_trace,
            "_break_page_for_pid",
            return_value=replay_trace.SplitPageResult(
                vaddr=0x3000,
                attempts=2,
                error=OSError(errno.ENOENT, "No such file or directory"),
            ),
        ):
            with self.assertLogs(replay_trace.logger, level="WARNING") as logs:
                result = replay_trace._invoke_break_page(
                    mock.sentinel.client,
                    4321,
                    "user-proof",
                    9,
                    2,
                )

        self.assertIn("after 2 attempts", "\n".join(logs.output))
        self.assertEqual(result.attempts, 2)

    def test_invoke_break_page_still_raises_for_vaptr_failures(self):
        with mock.patch.object(
            replay_trace,
            "_break_page_for_pid",
            side_effect=RuntimeError("VAPTR missing"),
        ):
            with self.assertRaisesRegex(RuntimeError, "VAPTR missing"):
                replay_trace._invoke_break_page(
                    mock.sentinel.client,
                    4321,
                    "user-proof",
                    9,
                    2,
                )

    def test_restore_snapshot_restarts_redis_with_snapshot(self):
        client = mock.Mock()
        client.info.return_value = {"process_id": 4321}
        client.config_get.side_effect = [
            {"dir": "/tmp/replay-redis-742"},
            {"dbfilename": "dump.rdb"},
        ]

        with (
            mock.patch.object(replay_trace.subprocess, "run") as run_mock,
            mock.patch.object(replay_trace.subprocess, "Popen") as popen_mock,
            mock.patch.object(replay_trace, "_wait_for_redis") as wait_mock,
            mock.patch.object(replay_trace, "_wait_for_redis_exit") as exit_wait_mock,
        ):
            replay_trace.restore_snapshot(
                client,
                Path("/tmp/snapshot.rdb"),
            )

        self.assertEqual(
            run_mock.call_args_list,
            [
                mock.call(
                    ["sudo", "rm", "-f", "/tmp/replay-redis-742/dump.rdb"],
                    check=True,
                ),
                mock.call(
                    ["sudo", "ln", "/tmp/snapshot.rdb", "/tmp/replay-redis-742/dump.rdb"],
                    check=True,
                ),
            ],
        )
        client.execute_command.assert_called_once_with("SHUTDOWN", "NOSAVE")
        exit_wait_mock.assert_called_once_with(4321)
        popen_args = popen_mock.call_args.args[0]
        self.assertEqual(
            popen_args[:6],
            [
                "redis-server",
                "./config/redis.conf",
                "--dir",
                "/tmp/replay-redis-742",
                "--dbfilename",
                "dump.rdb",
            ],
        )
        wait_mock.assert_called_once_with(
            client,
            timeout=replay_trace.REDIS_RESTORE_TIMEOUT_S,
        )

    def test_stage_snapshot_for_restore_fails_fast_when_hard_link_fails(self):
        with mock.patch.object(
            replay_trace.subprocess,
            "run",
            side_effect=[
                None,
                subprocess.CalledProcessError(1, ["sudo", "ln"]),
            ],
        ) as run_mock:
            with self.assertRaises(subprocess.CalledProcessError):
                replay_trace._stage_snapshot_for_restore(
                    Path("/tmp/snapshot.rdb"),
                    Path("/tmp/replay-redis-742/dump.rdb"),
                )

        self.assertEqual(
            run_mock.call_args_list,
            [
                mock.call(
                    ["sudo", "rm", "-f", "/tmp/replay-redis-742/dump.rdb"],
                    check=True,
                ),
                mock.call(
                    ["sudo", "ln", "/tmp/snapshot.rdb", "/tmp/replay-redis-742/dump.rdb"],
                    check=True,
                ),
            ],
        )

    def test_build_parser_keeps_replay_interface_simple(self):
        parser = replay_trace._build_parser()
        args = parser.parse_args(["snapshot.rdb", "run.log"])

        self.assertFalse(hasattr(args, "host"))
        self.assertFalse(hasattr(args, "port"))
        self.assertEqual(args.runs, 3)
        self.assertEqual(args.break_dispatch, "inline")
        self.assertIsNone(args.collector_config)

    def test_resolve_break_dispatch_uses_half_inline_then_threaded_for_mixed(self):
        self.assertEqual(
            replay_trace._resolve_break_dispatch(
                "mixed",
                run_number=1,
                runs=8,
            ),
            "inline",
        )
        self.assertEqual(
            replay_trace._resolve_break_dispatch(
                "mixed",
                run_number=4,
                runs=8,
            ),
            "inline",
        )
        self.assertEqual(
            replay_trace._resolve_break_dispatch(
                "mixed",
                run_number=5,
                runs=8,
            ),
            "threaded",
        )
        self.assertEqual(
            replay_trace._resolve_break_dispatch(
                "mixed",
                run_number=8,
                runs=8,
            ),
            "threaded",
        )

    def test_resolve_break_dispatch_fails_fast_for_odd_mixed_runs(self):
        with self.assertRaisesRegex(RuntimeError, "even --runs"):
            replay_trace._resolve_break_dispatch(
                "mixed",
                run_number=1,
                runs=3,
            )

    def test_run_benchmark_uses_repo_redis_config_and_passes_breakpoints(self):
        client = mock.Mock()
        breakpoints_df = pl.DataFrame(
            [
                {"user_hot": 0, "user_warm": 0},
                {"user_hot": 0, "user_warm": 10},
            ]
        )
        redis_endpoint = RedisEndpoint(host="10.1.2.3", port=6380)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "results.parquet"

            with (
                mock.patch.object(
                    replay_trace,
                    "load_repo_redis_endpoint",
                    return_value=redis_endpoint,
                ),
                mock.patch.object(
                    replay_trace.redis,
                    "Redis",
                    return_value=client,
                ) as redis_ctor_mock,
                mock.patch.object(
                    replay_trace,
                    "setup_system",
                    return_value=mock.sentinel.sys_config,
                ),
                mock.patch.object(
                    replay_trace,
                    "_set_thp_enabled_mode",
                ) as set_thp_mode_mock,
                mock.patch.object(replay_trace, "teardown_system"),
                mock.patch.object(replay_trace, "restore_snapshot") as restore_mock,
                mock.patch.object(replay_trace, "memory_purge"),
                mock.patch.object(
                    replay_trace,
                    "_get_redis_pid",
                    return_value=4321,
                ),
                mock.patch.object(
                    replay_trace,
                    "replay",
                    return_value=replay_trace.ReplayStats(
                        commands_replayed=42,
                        split_events=1,
                        split_successes=1,
                        split_failures=0,
                        split_syscall_attempts=1,
                        split_max_attempts=1,
                    ),
                ) as replay_mock,
            ):
                replay_trace.run_benchmark(
                    snapshot_path=Path("/tmp/snapshot.rdb"),
                    run_trace=Path("/tmp/monitor.log"),
                    breakpoints_df=breakpoints_df,
                    output=output_path,
                    runs=1,
                )

            redis_ctor_mock.assert_called_once_with(
                host="10.1.2.3",
                port=6380,
                decode_responses=True,
            )
            self.assertEqual(restore_mock.call_count, 2)
            self.assertEqual(
                set_thp_mode_mock.call_args_list,
                [mock.call("never"), mock.call("always")],
            )
            self.assertEqual(
                replay_mock.call_args_list,
                [
                    mock.call(
                        Path("/tmp/monitor.log"),
                        client,
                        breakpoints={},
                        break_dispatch="inline",
                    ),
                    mock.call(
                        Path("/tmp/monitor.log"),
                        client,
                        breakpoints={"user_hot": 0, "user_warm": 10},
                        break_dispatch="inline",
                    ),
                ],
            )

            df = pl.read_parquet(output_path)
            self.assertEqual(df["row_index"].to_list(), [0, 1])
            self.assertEqual(df["thp_mode"].to_list(), ["never", "always"])
            self.assertEqual(df["break_dispatch_1"].to_list(), ["inline", "inline"])
            self.assertEqual(df["commands_replayed_1"].to_list(), [42, 42])
            self.assertEqual(df["split_events_1"].to_list(), [1, 1])
            self.assertEqual(df["split_successes_1"].to_list(), [1, 1])
            self.assertEqual(df["split_failures_1"].to_list(), [0, 0])
            self.assertEqual(df["split_syscall_attempts_1"].to_list(), [1, 1])
            self.assertEqual(df["split_max_attempts_1"].to_list(), [1, 1])
            self.assertEqual(df["split_total_wall_ms_1"].to_list(), [0.0, 0.0])
            self.assertEqual(df["split_max_wall_ms_1"].to_list(), [0.0, 0.0])
            self.assertEqual(df["split_queue_lag_ms_mean_1"].to_list(), [0.0, 0.0])
            self.assertEqual(df["split_queue_lag_ms_max_1"].to_list(), [0.0, 0.0])

    def test_run_benchmark_writes_counter_columns_when_requested(self):
        client = mock.Mock()

        breakpoints_df = pl.DataFrame(
            [
                {"user_hot": 0},
                {"user_hot": 2},
            ]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "results.parquet"

            with (
                mock.patch.object(
                    replay_trace,
                    "load_repo_redis_endpoint",
                    return_value=RedisEndpoint(host="10.1.2.3", port=6380),
                ),
                mock.patch.object(
                    replay_trace.redis,
                    "Redis",
                    return_value=client,
                ),
                mock.patch.object(
                    replay_trace,
                    "setup_system",
                    return_value=mock.sentinel.sys_config,
                ),
                mock.patch.object(
                    replay_trace,
                    "_set_thp_enabled_mode",
                ) as set_thp_mode_mock,
                mock.patch.object(replay_trace, "teardown_system") as teardown_mock,
                mock.patch.object(replay_trace, "restore_snapshot") as restore_mock,
                mock.patch.object(replay_trace, "memory_purge") as purge_mock,
                mock.patch.object(
                    replay_trace,
                    "_get_redis_pid",
                    return_value=4321,
                ),
                mock.patch.object(
                    replay_trace,
                    "replay",
                    return_value=replay_trace.ReplayStats(
                        commands_replayed=42,
                        split_events=1,
                        split_successes=1,
                        split_failures=0,
                        split_syscall_attempts=2,
                        split_max_attempts=2,
                    ),
                ),
                mock.patch.object(
                    replay_trace,
                    "HardwareCollector",
                ) as hw_collector_cls_mock,
                mock.patch.object(
                    replay_trace.time,
                    "perf_counter",
                    side_effect=[10.0, 12.5, 20.0, 23.0],
                ),
            ):
                hw_instance = hw_collector_cls_mock.return_value
                hw_instance.stop_counters.return_value = {
                    "dtlb_loads": 101,
                    "dtlb_misses": 7,
                }
                replay_trace.run_benchmark(
                    snapshot_path=Path("/tmp/snapshot.rdb"),
                    run_trace=Path("/tmp/monitor.log"),
                    breakpoints_df=breakpoints_df,
                    output=output_path,
                    runs=1,
                    collectors=("dtlb_loads", "dtlb_misses"),
                )

            self.assertEqual(restore_mock.call_count, 2)
            self.assertEqual(purge_mock.call_count, 2)
            teardown_mock.assert_called_once_with(mock.sentinel.sys_config)
            self.assertEqual(
                set_thp_mode_mock.call_args_list,
                [mock.call("never"), mock.call("always")],
            )

            df = pl.read_parquet(output_path)
            self.assertEqual(df.height, 2)
            self.assertIn("runtime_s_1", df.columns)
            self.assertEqual(df["break_dispatch_1"].to_list(), ["inline", "inline"])
            self.assertEqual(df["split_syscall_attempts_1"].to_list(), [2, 2])
            self.assertEqual(df["split_max_attempts_1"].to_list(), [2, 2])
            self.assertEqual(df["split_total_wall_ms_1"].to_list(), [0.0, 0.0])
            self.assertEqual(df["split_queue_lag_ms_mean_1"].to_list(), [0.0, 0.0])
            self.assertEqual(df["dtlb_loads_1"][0], 101)
            self.assertEqual(df["dtlb_misses_1"][0], 7)
            self.assertEqual(df["dtlb_loads_1"][1], 101)
            self.assertEqual(df["dtlb_misses_1"][1], 7)
            self.assertEqual(
                hw_instance.start_counters.call_args_list,
                [
                    mock.call(["dtlb_loads", "dtlb_misses"], 4321),
                    mock.call(["dtlb_loads", "dtlb_misses"], 4321),
                ],
            )
            self.assertEqual(
                hw_instance.stop_counters.call_args_list,
                [
                    mock.call(["dtlb_loads", "dtlb_misses"]),
                    mock.call(["dtlb_loads", "dtlb_misses"]),
                ],
            )

    def test_run_benchmark_uses_base_pages_semantics_for_all_zero_row(self):
        client = mock.Mock()
        breakpoints_df = pl.DataFrame([{"user_hot": 0, "user_warm": 0}])

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "results.parquet"

            with (
                mock.patch.object(
                    replay_trace,
                    "load_repo_redis_endpoint",
                    return_value=RedisEndpoint(host="10.1.2.3", port=6380),
                ),
                mock.patch.object(
                    replay_trace.redis,
                    "Redis",
                    return_value=client,
                ),
                mock.patch.object(
                    replay_trace,
                    "setup_system",
                    return_value=mock.sentinel.sys_config,
                ),
                mock.patch.object(
                    replay_trace,
                    "_set_thp_enabled_mode",
                ) as set_thp_mode_mock,
                mock.patch.object(replay_trace, "teardown_system"),
                mock.patch.object(replay_trace, "restore_snapshot") as restore_mock,
                mock.patch.object(replay_trace, "memory_purge"),
                mock.patch.object(
                    replay_trace,
                    "_get_redis_pid",
                    return_value=4321,
                ),
                mock.patch.object(
                    replay_trace,
                    "replay",
                    return_value=replay_trace.ReplayStats(
                        commands_replayed=42,
                        split_events=0,
                        split_successes=0,
                        split_failures=0,
                        split_syscall_attempts=0,
                        split_max_attempts=0,
                    ),
                ) as replay_mock,
            ):
                replay_trace.run_benchmark(
                    snapshot_path=Path("/tmp/snapshot.rdb"),
                    run_trace=Path("/tmp/monitor.log"),
                    breakpoints_df=breakpoints_df,
                    output=output_path,
                    runs=1,
                )

            restore_mock.assert_called_once_with(
                client,
                Path("/tmp/snapshot.rdb"),
            )
            set_thp_mode_mock.assert_called_once_with("never")
            replay_mock.assert_called_once_with(
                Path("/tmp/monitor.log"),
                client,
                breakpoints={},
                break_dispatch="inline",
            )

    def test_run_benchmark_mixed_dispatch_writes_run_specific_columns(self):
        client = mock.Mock()
        breakpoints_df = pl.DataFrame([{"user_hot": 0}])

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "results.parquet"

            with (
                mock.patch.object(
                    replay_trace,
                    "load_repo_redis_endpoint",
                    return_value=RedisEndpoint(host="10.1.2.3", port=6380),
                ),
                mock.patch.object(
                    replay_trace.redis,
                    "Redis",
                    return_value=client,
                ),
                mock.patch.object(
                    replay_trace,
                    "setup_system",
                    return_value=mock.sentinel.sys_config,
                ),
                mock.patch.object(replay_trace, "_set_thp_enabled_mode"),
                mock.patch.object(replay_trace, "teardown_system"),
                mock.patch.object(replay_trace, "restore_snapshot"),
                mock.patch.object(replay_trace, "memory_purge"),
                mock.patch.object(
                    replay_trace,
                    "_get_redis_pid",
                    return_value=4321,
                ),
                mock.patch.object(
                    replay_trace,
                    "replay",
                    return_value=replay_trace.ReplayStats(
                        commands_replayed=42,
                        split_events=1,
                        split_successes=1,
                        split_failures=0,
                        split_syscall_attempts=2,
                        split_max_attempts=2,
                        split_total_wall_ms=12.5,
                        split_max_wall_ms=8.0,
                        split_queue_lag_ms_mean=1.5,
                        split_queue_lag_ms_max=3.0,
                    ),
                ) as replay_mock,
            ):
                replay_trace.run_benchmark(
                    snapshot_path=Path("/tmp/snapshot.rdb"),
                    run_trace=Path("/tmp/monitor.log"),
                    breakpoints_df=breakpoints_df,
                    output=output_path,
                    runs=2,
                    break_dispatch="mixed",
                )

            self.assertEqual(
                replay_mock.call_args_list,
                [
                    mock.call(
                        Path("/tmp/monitor.log"),
                        client,
                        breakpoints={},
                        break_dispatch="inline",
                    ),
                    mock.call(
                        Path("/tmp/monitor.log"),
                        client,
                        breakpoints={},
                        break_dispatch="threaded",
                    ),
                ],
            )

            df = pl.read_parquet(output_path)
            self.assertEqual(df["break_dispatch_1"].to_list(), ["inline"])
            self.assertEqual(df["break_dispatch_2"].to_list(), ["threaded"])
            self.assertEqual(df["split_total_wall_ms_1"].to_list(), [12.5])
            self.assertEqual(df["split_total_wall_ms_2"].to_list(), [12.5])
            self.assertEqual(df["split_max_wall_ms_1"].to_list(), [8.0])
            self.assertEqual(df["split_max_wall_ms_2"].to_list(), [8.0])
            self.assertEqual(df["split_queue_lag_ms_mean_1"].to_list(), [1.5])
            self.assertEqual(df["split_queue_lag_ms_mean_2"].to_list(), [1.5])
            self.assertEqual(df["split_queue_lag_ms_max_1"].to_list(), [3.0])
            self.assertEqual(df["split_queue_lag_ms_max_2"].to_list(), [3.0])

    def test_replay_returns_structured_stats_with_split_failures(self):
        log_text = (
            '1234.000001 [0 127.0.0.1:1] "HGET" "user-proof" "field0"\n'
            '1234.000002 [0 127.0.0.1:1] "ZREM" "_indices" "user-proof"\n'
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "monitor.log"
            trace_path.write_text(log_text, encoding="utf-8")

            with (
                mock.patch.object(
                    replay_trace,
                    "_get_redis_pid",
                    return_value=4321,
                ),
                mock.patch.object(
                    replay_trace,
                    "_invoke_break_page",
                    return_value=replay_trace.SplitPageResult(
                        vaddr=0x4000,
                        attempts=2,
                        error=OSError(errno.ENOENT, "No such file or directory"),
                    ),
                ) as break_mock,
                mock.patch.object(
                    replay_trace,
                    "_execute",
                    return_value=True,
                ) as execute_mock,
            ):
                stats = replay_trace.replay(
                    trace_path=trace_path,
                    client=mock.sentinel.client,
                    breakpoints={"user-proof": 0},
                    break_dispatch="inline",
                )

        break_mock.assert_called_once_with(
            mock.sentinel.client,
            4321,
            "user-proof",
            1,
            0,
        )
        self.assertEqual(execute_mock.call_count, 2)
        self.assertEqual(stats.commands_replayed, 2)
        self.assertEqual(stats.split_events, 1)
        self.assertEqual(stats.split_successes, 0)
        self.assertEqual(stats.split_failures, 1)
        self.assertEqual(stats.split_syscall_attempts, 2)
        self.assertEqual(stats.split_max_attempts, 2)
        self.assertGreaterEqual(stats.split_total_wall_ms, 0.0)
        self.assertGreaterEqual(stats.split_max_wall_ms, 0.0)
        self.assertEqual(stats.split_queue_lag_ms_mean, 0.0)
        self.assertEqual(stats.split_queue_lag_ms_max, 0.0)

    def test_replay_threaded_dispatch_records_queue_and_wall_time(self):
        log_text = '1234.000001 [0 127.0.0.1:1] "HGET" "user-proof" "field0"\n'

        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "monitor.log"
            trace_path.write_text(log_text, encoding="utf-8")
            client = mock.Mock()
            client.connection_pool.connection_kwargs = {
                "host": "127.0.0.1",
                "port": 6379,
                "decode_responses": True,
            }

            with (
                mock.patch.object(
                    replay_trace,
                    "_get_redis_pid",
                    return_value=4321,
                ),
                mock.patch.object(
                    replay_trace.redis,
                    "Redis",
                    return_value=mock.sentinel.thread_client,
                ),
                mock.patch.object(
                    replay_trace,
                    "_invoke_break_page",
                    return_value=replay_trace.SplitPageResult(
                        vaddr=0x4000,
                        attempts=1,
                        error=None,
                    ),
                ) as break_mock,
                mock.patch.object(
                    replay_trace,
                    "_execute",
                    return_value=True,
                ),
                mock.patch.object(
                    replay_trace.time,
                    "perf_counter",
                    side_effect=[1.0, 2.0, 2.25],
                ),
            ):
                stats = replay_trace.replay(
                    trace_path=trace_path,
                    client=client,
                    breakpoints={"user-proof": 0},
                    break_dispatch="threaded",
                )

        break_mock.assert_called_once_with(
            mock.sentinel.thread_client,
            4321,
            "user-proof",
            1,
            0,
        )
        self.assertEqual(stats.split_events, 1)
        self.assertEqual(stats.split_successes, 1)
        self.assertEqual(stats.split_failures, 0)
        self.assertAlmostEqual(stats.split_total_wall_ms, 250.0)
        self.assertAlmostEqual(stats.split_max_wall_ms, 250.0)
        self.assertAlmostEqual(stats.split_queue_lag_ms_mean, 1000.0)
        self.assertAlmostEqual(stats.split_queue_lag_ms_max, 1000.0)


if __name__ == "__main__":
    unittest.main()

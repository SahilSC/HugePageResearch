import shutil
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from data_schema import GraphEngine, demote
from kernmlops_benchmark.benchmark import Benchmark, GenericBenchmarkConfig
from kernmlops_benchmark.errors import (
    BenchmarkError,
    BenchmarkNotInCollectionData,
    BenchmarkNotRunningError,
    BenchmarkRunningError,
)
from kernmlops_config import ConfigBase
from pytimeparse.timeparse import timeparse
from redis_runtime import (
    RedisEndpoint,
    load_repo_redis_cli_path,
    load_repo_redis_endpoint,
    load_repo_redis_server_path,
)


@dataclass(frozen=True)
class RedisConfig(ConfigBase):
    repeat: int = 1
    outer_repeat: int = 1
    tcmalloc: bool = False
    # Core operation parameters
    field_count: int = 256
    field_length: int = 16
    min_field_length: int = 16
    operation_count: int = 1000000
    record_count: int = 1000000
    read_proportion: float = 0.5
    update_proportion: float = 0.5
    insert_proportion: float = 0.0
    rmw_proportion: float = 0.00
    scan_proportion: float = 0.00
    delete_proportion: float = 0.00

    # Distribution and performance parameters
    field_length_distribution: str = "uniform"
    request_distribution: str = "zipfian"
    insert_order: Literal["hashed", "ordered"] = "hashed"
    zero_padding: int = 1
    thread_count: int = 1
    target: int = 10000
    sleep: str | None = None
    server_sleep: str | None = None
    explicit_purge: bool = False
    load_from_rdb: bool = False
    vaptr_num_keys: int = 10
    vaptr_field_name: str = "field0"


class RedisBenchmark(Benchmark):
    FNV_OFFSET_BASIS_64 = 0xCBF29CE484222325
    FNV_PRIME_64 = 1099511628211

    @classmethod
    def name(cls) -> str:
        return "redis"

    @classmethod
    def default_config(cls) -> ConfigBase:
        return RedisConfig()

    @classmethod
    def from_config(cls, config: ConfigBase) -> "Benchmark":
        generic_config = cast(GenericBenchmarkConfig, getattr(config, "generic"))
        redis_config = cast(RedisConfig, getattr(config, cls.name()))
        return RedisBenchmark(generic_config=generic_config, config=redis_config)

    def redis_server_name(self) -> str:
        return "redis-server-tcmalloc" if self.config.tcmalloc else "redis-server"

    def __init__(self, *, generic_config: GenericBenchmarkConfig, config: RedisConfig):
        self.generic_config = generic_config
        self.config = config
        self.benchmark_dir = self.generic_config.get_benchmark_dir() / "ycsb"
        self.process: subprocess.Popen | None = None
        self.server: subprocess.Popen | None = None
        self._redis_endpoint_cache: RedisEndpoint | None = None
        self._log_file = None
        self._last_run_start: float | None = None
        self._last_run_indices: tuple[int, int] | None = None

    def _log(self, msg: str) -> None:
        """Write a timestamped line to the benchmark log file and flush."""
        if self._log_file is None:
            return
        self._log_file.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self._log_file.flush()

    def _log_text_block(self, title: str, text: str) -> None:
        """Write one multi-line text block to the benchmark log."""

        self._log(f"{title} begin")
        for line in text.splitlines():
            self._log(line)
        self._log(f"{title} end")

    def _log_config_text(self, config_text: str | None) -> None:
        """Write the full collection YAML to the benchmark log when available."""

        if config_text is None:
            self._log("Collection YAML config unavailable")
            return
        self._log_text_block("Collection YAML config", config_text)

    def _log_dbsize(self) -> None:
        """Run ``DBSIZE`` and record both the command and output in the log."""

        cmd = self._redis_cli_command("DBSIZE")
        self._log(f"Running command: {shlex.join(cmd)}")
        completed = subprocess.run(cmd, capture_output=True, text=True)
        self._log(f"Command exit={completed.returncode}")
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        self._log(f"Command stdout: {stdout if stdout else '<empty>'}")
        if stderr:
            self._log(f"Command stderr: {stderr}")

    def _log_active_run_finish(self) -> None:
        """Log the active run phase completion exactly once.

        Example output:
            [12:00:00] Run phase out_i=9 i=0 finished in 10.12s (exit=0)

        Raises:
            RuntimeError: If a run start time exists but its loop indices do not.
        """
        if self.process is None or self._last_run_start is None:
            return
        if self._last_run_indices is None:
            raise RuntimeError("Active redis run is missing loop indices")

        out_i, repeat_i = self._last_run_indices
        elapsed = time.time() - self._last_run_start
        self._log(
            f"Run phase out_i={out_i} i={repeat_i} "
            f"finished in {elapsed:.2f}s "
            f"(exit={self.process.returncode})"
        )
        self._last_run_start = None
        self._last_run_indices = None

    def is_configured(self) -> bool:
        return self.benchmark_dir.is_dir()

    def setup(self) -> None:
        if self.process is not None:
            raise BenchmarkRunningError()
        self.generic_config.generic_setup()

    def _redis_endpoint(self) -> RedisEndpoint:
        if self._redis_endpoint_cache is None:
            self._redis_endpoint_cache = load_repo_redis_endpoint()
        return self._redis_endpoint_cache

    def _redis_cli_command(self, *args: str) -> list[str]:
        endpoint = self._redis_endpoint()
        return [
            str(load_repo_redis_cli_path()),
            "-h",
            endpoint.host,
            "-p",
            str(endpoint.port),
            *args,
        ]

    def purge_server(self) -> None:
        # Purge Redis
        purge_redis = subprocess.run(self._redis_cli_command("MEMORY", "PURGE"))
        if purge_redis.returncode != 0:
            raise BenchmarkError("Redis Failed To Start")

    def _ensure_vaptr_module(self) -> Path:
        vaptr_dir = Path("./redis-module")
        vaptr_so = vaptr_dir / "vaptr.so"
        if vaptr_so.exists():
            return vaptr_so

        vaptr_src = vaptr_dir / "vaptr.c"
        if not vaptr_src.exists():
            raise BenchmarkError(
                "vaptr hook requested but redis-module/vaptr.c is missing"
            )

        build = subprocess.run(
            ["make", "-C", str(vaptr_dir)],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0 or not vaptr_so.exists():
            raise BenchmarkError(
                f"Failed to build redis vaptr module:\n{build.stdout}\n{build.stderr}"
            )
        return vaptr_so

    @staticmethod
    def _to_signed_64(value: int) -> int:
        value &= (1 << 64) - 1
        if value & (1 << 63):
            return value - (1 << 64)
        return value

    @classmethod
    def _ycsb_hash_keynum(cls, keynum: int) -> int:
        hashval = cls.FNV_OFFSET_BASIS_64
        value = keynum & ((1 << 64) - 1)

        for _ in range(8):
            octet = value & 0xFF
            value >>= 8
            hashval ^= octet
            hashval = (hashval * cls.FNV_PRIME_64) & ((1 << 64) - 1)

        signed_hash = cls._to_signed_64(hashval)
        if signed_hash == -(1 << 63):
            return signed_hash
        return abs(signed_hash)

    def _build_ycsb_key_name(self, keynum: int) -> str:
        if self.config.insert_order != "ordered":
            keynum = self._ycsb_hash_keynum(keynum)

        value = str(keynum)
        fill = max(self.config.zero_padding - len(value), 0)
        return f"user{'0' * fill}{value}"

    def vaptr_key_names(self, count: int | None = None) -> list[str]:
        if count is None:
            count = self.config.vaptr_num_keys
        if count <= 0:
            return []

        # Track keys from the first load batch so the hook knows the cohort before the run starts.
        max_keys = min(count, self.config.record_count)
        return [self._build_ycsb_key_name(keynum) for keynum in range(max_keys)]

    def run(
        self,
        *,
        run_dir: Path | None = None,
        config_text: str | None = None,
    ) -> None:
        if self.process is not None:
            raise BenchmarkRunningError()
        if self.server is not None:
            raise BenchmarkRunningError()
        endpoint = self._redis_endpoint()

        if run_dir is not None:
            run_dir.mkdir(parents=True, exist_ok=True)
            log_path = run_dir / "redis_benchmark.log"
        else:
            log_path = Path("redis_benchmark.log")
        self._log_file = open(log_path, "w")
        self._log(f"Redis benchmark started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self._log_config_text(config_text)

        if not self.config.load_from_rdb:
            dump = Path("dump.rdb")
            if dump.exists():
                shutil.move(dump, dump.with_suffix(".rdb.bak"))

        # Kill any existing redis-server on the configured endpoint.
        subprocess.run(
            self._redis_cli_command("SHUTDOWN", "NOSAVE"),
            capture_output=True,
            timeout=5,
        )
        time.sleep(1)

        # start the redis server
        start_redis = [
            str(load_repo_redis_server_path(tcmalloc=self.config.tcmalloc)),
            "./config/redis.conf",
        ]
        vaptr_so = Path("./redis-module/vaptr.so")
        if vaptr_so.exists():
            start_redis += ["--loadmodule", str(vaptr_so.resolve())]
        self.server = subprocess.Popen(
            start_redis, stdout=self._log_file, stderr=self._log_file,
        )

        # Wait for redis
        time.sleep(1)
        ping_redis = subprocess.run(self._redis_cli_command("ping"))
        i = 0
        while i < 10 and ping_redis.returncode != 0:
            time.sleep(1)
            ping_redis = subprocess.run(self._redis_cli_command("ping"))
            i += 1

        if ping_redis.returncode != 0:
            raise BenchmarkError("Redis Failed To Start")

        server_space: int | float | None = (
            None
            if self.config.server_sleep is None
            else timeparse(self.config.server_sleep)
        )
        if server_space is not None:
            time.sleep(server_space)

        space: int | float | None = (
            None if self.config.sleep is None else timeparse(self.config.sleep)
        )
        process: subprocess.Popen | None = None
        for out_i in range(self.config.outer_repeat):
            for i in range(self.config.repeat):
                if process is not None:
                    process.wait()
                    self._log_active_run_finish()
                    if self.config.explicit_purge:
                        self.purge_server()
                    if space is not None:
                        time.sleep(space)
                    if process.returncode != 0:
                        self.process = process
                        raise BenchmarkError(f"Redis Run {(2 * i) - 1} Failed")

                insert_start = out_i * self.config.record_count
                # Load Server
                load_redis = [
                    "python",
                    f"{self.benchmark_dir}/YCSB/bin/ycsb",
                    "load",
                    "redis",
                    "-s",
                    "-P",
                    f"{self.benchmark_dir}/YCSB/workloads/workloada",
                    "-p",
                    f"redis.host={endpoint.host}",
                    "-p",
                    f"redis.port={endpoint.port}",
                    "-p",
                    f"recordcount={self.config.record_count}",
                    "-p",
                    f"fieldcount={self.config.field_count}",
                    "-p",
                    f"fieldlength={self.config.field_length}",
                    "-p",
                    f"minfieldlength={self.config.min_field_length}",
                    "-p",
                    f"insertstart={insert_start}",
                    "-p",
                    f"insertorder={self.config.insert_order}",
                    "-p",
                    f"zeropadding={self.config.zero_padding}",
                    "-p",
                    f"fieldlengthdistribution={self.config.field_length_distribution}",
                ]


                if i == 0:
                    self._log(f"Load phase out_i={out_i} starting "
                              f"(insert_start={insert_start})")
                    load_start = time.time()
                    load_redis = subprocess.Popen(
                        load_redis, preexec_fn=demote(),
                        stdout=self._log_file, stderr=self._log_file,
                    )

                    load_redis.wait()
                    load_elapsed = time.time() - load_start
                    self._log(f"Load phase out_i={out_i} finished in "
                              f"{load_elapsed:.2f}s "
                              f"(exit={load_redis.returncode})")
                    if load_redis.returncode != 0:
                        raise BenchmarkError("Loading Redis Failing")

                    if self.config.explicit_purge:
                        self.purge_server()

                self._log_dbsize()

                record_count = (out_i + 1) * self.config.record_count
                run_redis = [
                    f"{self.benchmark_dir}/YCSB/bin/ycsb",
                    "run",
                    "redis",
                    "-s",
                    "-P",
                    f"{self.benchmark_dir}/YCSB/workloads/workloada",
                    "-p",
                    f"operationcount={self.config.operation_count}",
                    "-p",
                    f"recordcount={record_count}",
                    "-p",
                    "workload=site.ycsb.workloads.CoreWorkload",
                    "-p",
                    f"readproportion={self.config.read_proportion}",
                    "-p",
                    f"updateproportion={self.config.update_proportion}",
                    "-p",
                    f"insertproportion={self.config.insert_proportion}",
                    "-p",
                    f"readmodifywriteproportion={self.config.rmw_proportion}",
                    "-p",
                    f"scanproportion={self.config.scan_proportion}",
                    "-p",
                    f"deleteproportion={self.config.delete_proportion}",
                    "-p",
                    f"redis.host={endpoint.host}",
                    "-p",
                    f"redis.port={endpoint.port}",
                    "-p",
                    f"requestdistribution={self.config.request_distribution}",
                    "-p",
                    f"insertorder={self.config.insert_order}",
                    "-p",
                    f"zeropadding={self.config.zero_padding}",
                    "-p",
                    f"threadcount={self.config.thread_count}",
                    "-p",
                    f"target={self.config.target}",
                    "-p",
                    f"fieldcount={self.config.field_count}",
                    "-p",
                    f"fieldlength={self.config.field_length}",
                    "-p",
                    f"minfieldlength={self.config.min_field_length}",
                    "-p",
                    f"fieldlengthdistribution={self.config.field_length_distribution}",
                ]
                self._log(f"Run phase out_i={out_i} i={i} starting")
                run_start = time.time()
                self._last_run_indices = (out_i, i)
                process = subprocess.Popen(
                    run_redis, preexec_fn=demote(),
                    stdout=self._log_file, stderr=self._log_file,
                )
        self.process = process
        self._last_run_start = run_start

    def poll(self) -> int | None:
        if self.process is None:
            raise BenchmarkNotRunningError()
        ret = self.process.poll()
        if ret is None:
            return ret
        self._log_active_run_finish()
        self.end_server()
        return ret

    def wait(self) -> None:
        if self.process is None:
            raise BenchmarkNotRunningError()
        self.process.wait()
        self._log_active_run_finish()
        self.end_server()

    def kill(self) -> None:
        if self.process is None:
            raise BenchmarkNotRunningError()
        self.process.terminate()
        self.end_server()

    def end_server(self) -> None:
        if self.server is None:
            return
        self._log_dbsize()
        self.server.send_signal(signal.SIGINT)
        try:
            self.server.wait(10)
        except subprocess.TimeoutExpired:
            self.server.terminate()

        self.server = None
        if self._log_file is not None:
            self._log(f"Redis benchmark finished at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            self._log_file.close()
            self._log_file = None

    @classmethod
    def plot_events(cls, graph_engine: GraphEngine) -> None:
        if graph_engine.collection_data.benchmark != cls.name():
            raise BenchmarkNotInCollectionData()

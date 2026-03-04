import subprocess
import sys
import time
from dataclasses import dataclass

import polars as pl
from data_collection.bpf_instrumentation.bpf_hook import BPFProgram
from data_schema import CollectionTable
from data_schema.vaptr import VAPtrTable


@dataclass(frozen=True)
class VAPtrData:
    ts_uptime_us: int
    key: str
    address: str
    page_addr: str


class VAPtrHook(BPFProgram):
    @classmethod
    def name(cls) -> str:
        return "vaptr"

    def __init__(
        self,
        num_keys: int = 10,
        redis_host: str = "127.0.0.1",
        redis_port: int = 6380,
    ):
        self.num_keys = num_keys
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.collection_id = ""
        self.samples = list[VAPtrData]()
        self.key_names = [f"user{i:016d}" for i in range(num_keys)]
        self.key_scan_pattern = "user*"
        self.key_refresh_interval_ns = 2_000_000_000
        self.last_key_refresh_ns = 0
        self.module_verified = False

    def load(self, collection_id: str):
        self.collection_id = collection_id

    def _redis_cli_base_cmd(self) -> list[str]:
        return [
            "redis-cli",
            "-h",
            self.redis_host,
            "-p",
            str(self.redis_port),
        ]

    def _refresh_key_names(self) -> None:
        now_ns = time.monotonic_ns()
        if now_ns - self.last_key_refresh_ns < self.key_refresh_interval_ns:
            return

        self.last_key_refresh_ns = now_ns
        try:
            result = subprocess.run(
                [
                    *self._redis_cli_base_cmd(),
                    "--scan",
                    "--pattern",
                    self.key_scan_pattern,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return

        if result.returncode != 0:
            return

        scanned_keys = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
        if not scanned_keys:
            return

        unique_keys = list(dict.fromkeys(scanned_keys))
        self.key_names = unique_keys[: self.num_keys]

    def _run_vaptr(self, keys: list[str]) -> subprocess.CompletedProcess[str] | None:
        cmd = [
            *self._redis_cli_base_cmd(),
            "VAPTR",
            *keys,
        ]
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def poll(self):
        self._refresh_key_names()
        keys = self.key_names[: self.num_keys]
        if not keys:
            return

        result = self._run_vaptr(keys)
        if result is None:
            return

        output = result.stdout.strip()

        # Check if module is loaded on first successful connection
        if not self.module_verified:
            if "ERR unknown command" in output or "ERR unknown command" in result.stderr:
                print(
                    "vaptr: ERROR - VAPTR command not recognized by redis-server. "
                    "Is the vaptr.so module loaded? Check that config/redis.conf has "
                    "'loadmodule ./redis-module/vaptr.so' and that the benchmark's "
                    f"redis-server (not system redis) is running on port {self.redis_port}.",
                    file=sys.stderr,
                )
                return
            if output:
                self.module_verified = True

        if result.returncode != 0:
            return

        if not output:
            return

        ts_uptime_us = int(time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1000)
        lines = output.splitlines()

        # redis-cli output format (no numbering, flat):
        #   user0000000000000000
        #   0x7f8a4c001234
        #   user0000000000000001
        #   0x7f8a4c005678
        #   user0000000000000099
        #   (nil)
        # Lines come in pairs: key name, then address or (nil)
        for i in range(0, len(lines) - 1, 2):
            key_name = lines[i].strip()
            addr_str = lines[i + 1].strip()
            if not addr_str.startswith("0x"):
                continue
            try:
                addr_int = int(addr_str, 16)
            except ValueError:
                continue
            page_addr = f"0x{addr_int & ~0xFFF:x}"
            self.samples.append(
                VAPtrData(
                    ts_uptime_us=ts_uptime_us,
                    key=key_name,
                    address=addr_str,
                    page_addr=page_addr,
                )
            )

    def close(self):
        pass

    def data(self) -> list[CollectionTable]:
        if not self.samples:
            return []
        return [
            VAPtrTable.from_df_id(
                pl.DataFrame(self.samples, schema=VAPtrTable.schema()),
                collection_id=self.collection_id,
            )
        ]

    def clear(self):
        self.samples.clear()

    def pop_data(self) -> list[CollectionTable]:
        tables = self.data()
        self.clear()
        return tables

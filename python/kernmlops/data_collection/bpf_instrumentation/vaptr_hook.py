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

    def __init__(self, num_keys: int = 10, field_name: str = "field0"):
        self.num_keys = num_keys
        self.field_name = field_name
        self.collection_id = ""
        self.samples = list[VAPtrData]()
        self.key_names: list[str] = []
        self.module_verified = False

    def load(self, collection_id: str):
        self.collection_id = collection_id

    def _discover_keys(self) -> list[str]:
        if self.num_keys <= 0:
            return []

        seen = set[str]()
        cursor = "0"

        while True:
            cmd = [
                "redis-cli",
                "--raw",
                "SCAN",
                cursor,
                "MATCH",
                "user*",
                "COUNT",
                str(max(self.num_keys * 2, 10)),
            ]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return []

            if result.returncode != 0:
                return []

            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if not lines:
                return []

            cursor = lines[0]
            for key_name in lines[1:]:
                if key_name in seen:
                    continue
                seen.add(key_name)
                if len(seen) >= self.num_keys:
                    return sorted(seen)

            if cursor == "0":
                return sorted(seen)

    def _run_vaptr(self) -> subprocess.CompletedProcess[str] | None:
        cmd = ["redis-cli", "--raw", "VAPTR", "FIELD", self.field_name] + self.key_names
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
        if not self.key_names:
            self.key_names = self._discover_keys()
        if not self.key_names:
            return

        result = self._run_vaptr()
        if result is None:
            return

        output = result.stdout.strip()

        # Check if module is loaded on first successful connection
        if not self.module_verified:
            if "ERR unknown command" in output or "ERR unknown command" in result.stderr:
                print(
                    "vaptr: ERROR - VAPTR command not recognized by redis-server. "
                    "Is the vaptr.so module loaded? The redis benchmark now auto-builds "
                    "and loads ./redis-module/vaptr.so; verify that benchmark-managed "
                    "redis-server (not system redis) is the one running on port 6379.",
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

        # redis-cli --raw output format (no numbering, flat):
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

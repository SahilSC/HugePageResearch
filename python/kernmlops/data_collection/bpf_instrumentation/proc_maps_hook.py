import re
import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from data_collection.bpf_instrumentation.bpf_hook import BPFProgram
from data_schema import CollectionTable
from data_schema.proc_maps import ProcMapsTable


@dataclass(frozen=True)
class ProcMapsData:
    pid: int
    tgid: int
    ts_uptime_us: int
    start_addr: int
    end_addr: int
    size_kb: int
    perms: str
    offset: int
    dev: str
    inode: int
    pathname: str


class ProcMapsHook(BPFProgram):
    @classmethod
    def name(cls) -> str:
        return "proc_maps"

    def __init__(self, process_name: str):
        self.target_pid = None
        self.pid_regex = re.compile(process_name)
        self.collection_id = ""
        self.samples = list[ProcMapsData]()

    def load(self, collection_id: str):
        self.collection_id = collection_id

    def _find_target_pid(self) -> int | None:
        proc = Path("/proc")
        candidates = []
        for pid_dir in proc.iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                comm = (pid_dir / "comm").read_text().strip()
            except Exception:
                continue
            if self.pid_regex.search(comm):
                candidates.append(int(pid_dir.name))
        if not candidates:
            return None
        return max(candidates)

    def _parse_maps_line(
        self, line: str, pid: int, ts_uptime_us: int
    ) -> ProcMapsData | None:
        # Format: start-end perms offset dev inode [pathname]
        # Example: 7f8a4c000000-7f8a4c100000 rw-p 00000000 fd:01 12345 /usr/lib/libc.so.6
        parts = line.split(None, 5)
        if len(parts) < 5:
            return None
        addr_range = parts[0].split("-")
        if len(addr_range) != 2:
            return None
        start_addr = int(addr_range[0], 16)
        end_addr = int(addr_range[1], 16)
        return ProcMapsData(
            pid=pid,
            tgid=pid,
            ts_uptime_us=ts_uptime_us,
            start_addr=start_addr,
            end_addr=end_addr,
            size_kb=(end_addr - start_addr) // 1024,
            perms=parts[1],
            offset=int(parts[2], 16),
            dev=parts[3],
            inode=int(parts[4]),
            pathname=parts[5].strip() if len(parts) > 5 else "",
        )

    def poll(self):
        if self.target_pid is None:
            self.target_pid = self._find_target_pid()
        if self.target_pid is None:
            raise RuntimeError(
                f"proc_maps: no process matching '{self.pid_regex.pattern}' found in /proc"
            )

        pid = self.target_pid
        maps_path = Path(f"/proc/{pid}/maps")
        try:
            raw = maps_path.read_text()
        except Exception:
            return
        ts_uptime_us = int(time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1000)
        for line in raw.splitlines():
            entry = self._parse_maps_line(line, pid, ts_uptime_us)
            if entry is not None:
                self.samples.append(entry)

    def close(self):
        pass

    def data(self) -> list[CollectionTable]:
        if not self.samples:
            return []
        return [
            ProcMapsTable.from_df_id(
                pl.DataFrame(self.samples, schema=ProcMapsTable.schema()),
                collection_id=self.collection_id,
            )
        ]

    def clear(self):
        self.samples.clear()

    def pop_data(self) -> list[CollectionTable]:
        tables = self.data()
        self.clear()
        return tables

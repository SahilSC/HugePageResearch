import re
import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from data_collection.bpf_instrumentation.bpf_hook import BPFProgram
from data_schema import CollectionTable
from data_schema.thp_harness import SmapsRollupSampleTable


@dataclass(frozen=True)
class SmapsRollupSample:
    pid: int
    tgid: int
    ts_ns: int
    rss_kb: int
    pss_kb: int
    anonymous_kb: int
    referenced_kb: int
    anon_hugepages_kb: int
    ra_pages_kb: int
    ra_state: int


class SmapsRollupHook(BPFProgram):
    @classmethod
    def name(cls) -> str:
        return "smaps_rollup_hook"

    def __init__(self, process_name: str):
        self.collection_id = ""
        self.pid_regex = re.compile(process_name)
        self.target_pid: int | None = None
        self.samples = list[SmapsRollupSample]()

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

    @staticmethod
    def _parse_rollup_value(raw: str, key: str) -> int:
        pattern = f"{key}:"
        for line in raw.splitlines():
            if line.startswith(pattern):
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1])
        return 0

    def poll(self):
        if self.target_pid is None:
            self.target_pid = self._find_target_pid()
        if self.target_pid is None:
            return

        rollup_path = Path(f"/proc/{self.target_pid}/smaps_rollup")
        try:
            raw = rollup_path.read_text()
        except Exception:
            self.target_pid = None
            return

        now_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        self.samples.append(
            SmapsRollupSample(
                pid=self.target_pid,
                tgid=self.target_pid,
                ts_ns=now_ns,
                rss_kb=self._parse_rollup_value(raw, "Rss"),
                pss_kb=self._parse_rollup_value(raw, "Pss"),
                anonymous_kb=self._parse_rollup_value(raw, "Anonymous"),
                referenced_kb=self._parse_rollup_value(raw, "Referenced"),
                anon_hugepages_kb=self._parse_rollup_value(raw, "AnonHugePages"),
                ra_pages_kb=self._parse_rollup_value(raw, "Ra_pages"),
                ra_state=self._parse_rollup_value(raw, "Ra_state"),
            )
        )

    def close(self):
        pass

    def data(self) -> list[CollectionTable]:
        if not self.samples:
            return []
        return [
            SmapsRollupSampleTable.from_df_id(
                pl.DataFrame(self.samples),
                collection_id=self.collection_id,
            )
        ]

    def clear(self):
        self.samples.clear()

    def pop_data(self) -> list[CollectionTable]:
        sample_tables = self.data()
        self.clear()
        return sample_tables

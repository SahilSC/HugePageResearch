import re
import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from data_collection.bpf_instrumentation.bpf_hook import BPFProgram
from data_schema import CollectionTable
from data_schema.proc_smaps import ProcSmapsTable


@dataclass(frozen=True)
class ProcSmapsVMARegionData:
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
    kernel_page_size_kb: int
    mmu_page_size_kb: int
    rss_kb: int
    pss_kb: int
    pss_dirty_kb: int
    shared_clean_kb: int
    shared_dirty_kb: int
    private_clean_kb: int
    private_dirty_kb: int
    referenced_kb: int
    anonymous_kb: int
    ksm_kb: int
    lazy_free_kb: int
    anon_hugepages_kb: int
    shmem_pmd_mapped_kb: int
    file_pmd_mapped_kb: int
    shared_hugetlb_kb: int
    private_hugetlb_kb: int
    swap_kb: int
    swap_pss_kb: int
    locked_kb: int
    thp_eligible: int
    protection_key: int
    vm_flags: str


# Mapping from smaps key prefix to dataclass field name
_SMAPS_KEY_MAP: dict[str, str] = {
    "Size:": "size_kb",
    "KernelPageSize:": "kernel_page_size_kb",
    "MMUPageSize:": "mmu_page_size_kb",
    "Rss:": "rss_kb",
    "Pss:": "pss_kb",
    "Pss_Dirty:": "pss_dirty_kb",
    "Shared_Clean:": "shared_clean_kb",
    "Shared_Dirty:": "shared_dirty_kb",
    "Private_Clean:": "private_clean_kb",
    "Private_Dirty:": "private_dirty_kb",
    "Referenced:": "referenced_kb",
    "Anonymous:": "anonymous_kb",
    "KSM:": "ksm_kb",
    "LazyFree:": "lazy_free_kb",
    "AnonHugePages:": "anon_hugepages_kb",
    "ShmemPmdMapped:": "shmem_pmd_mapped_kb",
    "FilePmdMapped:": "file_pmd_mapped_kb",
    "Shared_Hugetlb:": "shared_hugetlb_kb",
    "Private_Hugetlb:": "private_hugetlb_kb",
    "Swap:": "swap_kb",
    "SwapPss:": "swap_pss_kb",
    "Locked:": "locked_kb",
    "THPeligible:": "thp_eligible",
    "ProtectionKey:": "protection_key",
}


class ProcSmapsHook(BPFProgram):
    @classmethod
    def name(cls) -> str:
        return "smaps_hook"

    def __init__(self, process_name: str):
        self.target_pid: int | None = None
        self.pid_regex = re.compile(process_name)
        self.collection_id = ""
        self.samples = list[ProcSmapsVMARegionData]()

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

    def poll(self):
        if self.target_pid is None:
            self.target_pid = self._find_target_pid()
        if self.target_pid is None:
            raise RuntimeError(
                f"smaps_hook: no process matching '{self.pid_regex.pattern}' found in /proc"
            )

        pid = self.target_pid
        smaps_path = Path(f"/proc/{pid}/smaps")
        try:
            raw = smaps_path.read_text()
        except Exception:
            return

        ts_uptime_us = int(time.clock_gettime_ns(time.CLOCK_BOOTTIME) / 1000)
        lines = raw.splitlines()

        # Parse state for current VMA
        header: dict[str, object] | None = None
        fields: dict[str, int | str] = {}

        def emit():
            if header is None:
                return
            self.samples.append(
                ProcSmapsVMARegionData(
                    pid=pid,
                    tgid=pid,
                    ts_uptime_us=ts_uptime_us,
                    start_addr=header["start_addr"],  # type: ignore[arg-type]
                    end_addr=header["end_addr"],  # type: ignore[arg-type]
                    size_kb=fields.get("size_kb", (header["end_addr"] - header["start_addr"]) // 1024),  # type: ignore[operator]
                    perms=header["perms"],  # type: ignore[arg-type]
                    offset=header["offset"],  # type: ignore[arg-type]
                    dev=header["dev"],  # type: ignore[arg-type]
                    inode=header["inode"],  # type: ignore[arg-type]
                    pathname=header["pathname"],  # type: ignore[arg-type]
                    kernel_page_size_kb=fields.get("kernel_page_size_kb", 0),  # type: ignore[arg-type]
                    mmu_page_size_kb=fields.get("mmu_page_size_kb", 0),  # type: ignore[arg-type]
                    rss_kb=fields.get("rss_kb", 0),  # type: ignore[arg-type]
                    pss_kb=fields.get("pss_kb", 0),  # type: ignore[arg-type]
                    pss_dirty_kb=fields.get("pss_dirty_kb", 0),  # type: ignore[arg-type]
                    shared_clean_kb=fields.get("shared_clean_kb", 0),  # type: ignore[arg-type]
                    shared_dirty_kb=fields.get("shared_dirty_kb", 0),  # type: ignore[arg-type]
                    private_clean_kb=fields.get("private_clean_kb", 0),  # type: ignore[arg-type]
                    private_dirty_kb=fields.get("private_dirty_kb", 0),  # type: ignore[arg-type]
                    referenced_kb=fields.get("referenced_kb", 0),  # type: ignore[arg-type]
                    anonymous_kb=fields.get("anonymous_kb", 0),  # type: ignore[arg-type]
                    ksm_kb=fields.get("ksm_kb", 0),  # type: ignore[arg-type]
                    lazy_free_kb=fields.get("lazy_free_kb", 0),  # type: ignore[arg-type]
                    anon_hugepages_kb=fields.get("anon_hugepages_kb", 0),  # type: ignore[arg-type]
                    shmem_pmd_mapped_kb=fields.get("shmem_pmd_mapped_kb", 0),  # type: ignore[arg-type]
                    file_pmd_mapped_kb=fields.get("file_pmd_mapped_kb", 0),  # type: ignore[arg-type]
                    shared_hugetlb_kb=fields.get("shared_hugetlb_kb", 0),  # type: ignore[arg-type]
                    private_hugetlb_kb=fields.get("private_hugetlb_kb", 0),  # type: ignore[arg-type]
                    swap_kb=fields.get("swap_kb", 0),  # type: ignore[arg-type]
                    swap_pss_kb=fields.get("swap_pss_kb", 0),  # type: ignore[arg-type]
                    locked_kb=fields.get("locked_kb", 0),  # type: ignore[arg-type]
                    thp_eligible=fields.get("thp_eligible", 0),  # type: ignore[arg-type]
                    protection_key=fields.get("protection_key", 0),  # type: ignore[arg-type]
                    vm_flags=fields.get("vm_flags", ""),  # type: ignore[arg-type]
                )
            )

        for line in lines:
            if not line:
                continue
            # Header line: "start-end perms offset dev inode [pathname]"
            if "-" in line and len(line) > 1 and line[0].isalnum() and line[1].isalnum():
                emit()
                parts = line.split(None, 5)
                if len(parts) < 5:
                    header = None
                    continue
                addr_range = parts[0].split("-")
                if len(addr_range) != 2:
                    header = None
                    continue
                start_addr = int(addr_range[0], 16)
                end_addr = int(addr_range[1], 16)
                header = {
                    "start_addr": start_addr,
                    "end_addr": end_addr,
                    "perms": parts[1],
                    "offset": int(parts[2], 16),
                    "dev": parts[3],
                    "inode": int(parts[4]),
                    "pathname": parts[5].strip() if len(parts) > 5 else "",
                }
                fields = {}
                continue
            # VmFlags line (no numeric value in kB)
            if line.startswith("VmFlags:"):
                fields["vm_flags"] = line.replace("VmFlags:", "").strip()
                continue
            # Key: value kB lines
            for prefix, field_name in _SMAPS_KEY_MAP.items():
                if line.startswith(prefix):
                    parts = line.split()
                    if len(parts) >= 2:
                        fields[field_name] = int(parts[1])
                    break

        emit()

    def close(self):
        pass

    def data(self) -> list[CollectionTable]:
        if not self.samples:
            return []
        return [
            ProcSmapsTable.from_df_id(
                pl.DataFrame(self.samples, schema=ProcSmapsTable.schema()),
                collection_id=self.collection_id,
            )
        ]

    def clear(self):
        self.samples.clear()

    def pop_data(self) -> list[CollectionTable]:
        tables = self.data()
        self.clear()
        return tables

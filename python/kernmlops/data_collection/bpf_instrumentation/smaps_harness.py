import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
from data_collection.bpf_instrumentation.bpf_hook import BPFProgram
from data_schema import CollectionTable
from data_schema.thp_harness import (
    SmapsRollupSampleTable,
    SmapsVMARegionSampleTable,
    THPCandidateTable,
    THPInterventionEventTable,
    age_bucket_for,
)


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


@dataclass(frozen=True)
class SmapsVMARegionSample:
    pid: int
    tgid: int
    ts_ns: int
    start_addr: int
    end_addr: int
    rss_kb: int
    anonymous_kb: int
    referenced_kb: int
    anon_hugepages_kb: int
    thp_eligible: int
    vm_flags: str


@dataclass(frozen=True)
class THPCandidateRow:
    decision_id: str
    pid: int
    tgid: int
    ts_ns: int
    candidate_type: str
    mm: str
    pfn: int
    start_addr: int
    end_addr: int
    age_sec: float
    age_censored: bool
    referenced: int
    none_or_zero: int
    writable: int
    scan_status: int
    anon_hugepages_kb: int
    thp_eligible: int
    recent_scan_hit: int


@dataclass(frozen=True)
class THPInterventionRow:
    intervention_id: str
    decision_id: str
    pid: int
    tgid: int
    ts_ns: int
    start_addr: int
    end_addr: int
    age_bucket: str
    success: bool
    error: str
    command: str


class SmapsHarnessHook(BPFProgram):
    @classmethod
    def name(cls) -> str:
        return "smaps_harness"

    def __init__(self, hugepage_harness: Any | None = None):
        self.collection_id = ""
        self.pid_regex = re.compile(
            getattr(hugepage_harness, "redis_name_regex", "redis-server")
        )
        self.rollup_interval_ns = int(
            getattr(hugepage_harness, "smaps_rollup_interval_ms", 500) * 1e6
        )
        self.vma_interval_ns = int(
            getattr(hugepage_harness, "smaps_vma_interval_ms", 2000) * 1e6
        )
        self.negative_sample_interval_ns = int(
            getattr(hugepage_harness, "negative_sample_period_s", 2) * 1e9
        )
        self.intervention_enabled = bool(
            getattr(hugepage_harness, "intervention_enabled", True)
        )
        self.intervention_period_ns = int(
            getattr(hugepage_harness, "intervention_period_s", 10) * 1e9
        )
        self.intervention_warmup_ns = int(
            getattr(hugepage_harness, "intervention_warmup_s", 30) * 1e9
        )
        self.split_debugfs_path = Path(
            getattr(
                hugepage_harness,
                "split_debugfs_path",
                "/sys/kernel/debug/split_huge_pages",
            )
        )
        age_weights = list(
            getattr(hugepage_harness, "age_bucket_weights", [0.4, 0.4, 0.2])
        )
        self.age_bucket_weights = {
            "young": float(age_weights[0]) if len(age_weights) > 0 else 0.4,
            "mid": float(age_weights[1]) if len(age_weights) > 1 else 0.4,
            "old": float(age_weights[2]) if len(age_weights) > 2 else 0.2,
        }
        seed = int(getattr(hugepage_harness, "random_seed", 17))
        self.rand = random.Random(seed)

        self.rollup_samples = list[SmapsRollupSample]()
        self.vma_samples = list[SmapsVMARegionSample]()
        self.candidates = list[THPCandidateRow]()
        self.interventions = list[THPInterventionRow]()

        self.last_rollup_ns = 0
        self.last_vma_ns = 0
        self.last_negative_sample_ns = 0
        self.last_intervention_ns = 0
        self.started_ns = 0

        self.decision_counter = 0
        self.intervention_counter = 0

        self.first_seen_ns = dict[tuple[int, int], int]()
        self.last_seen_ref = dict[tuple[int, int], int]()
        self.last_seen_ts = dict[tuple[int, int], int]()

    def load(self, collection_id: str):
        self.collection_id = collection_id
        self.started_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)

    def _find_target_pid(self) -> int | None:
        proc = Path("/proc")
        candidates = []
        for pid_dir in proc.iterdir():
            if not pid_dir.name.isdigit():
                continue
            comm_path = pid_dir / "comm"
            try:
                comm = comm_path.read_text().strip()
            except Exception:
                continue
            if self.pid_regex.search(comm):
                candidates.append(int(pid_dir.name))
        if not candidates:
            return None
        return max(candidates)

    def _parse_rollup_value(self, raw: str, key: str) -> int:
        pattern = f"{key}:"
        for line in raw.splitlines():
            if line.startswith(pattern):
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1])
        return 0

    def _sample_rollup(self, pid: int, now_ns: int):
        rollup_path = Path(f"/proc/{pid}/smaps_rollup")
        try:
            raw = rollup_path.read_text()
        except Exception:
            return
        self.rollup_samples.append(
            SmapsRollupSample(
                pid=pid,
                tgid=pid,
                ts_ns=now_ns,
                rss_kb=self._parse_rollup_value(raw, "Rss"),
                pss_kb=self._parse_rollup_value(raw, "Pss"),
                anonymous_kb=self._parse_rollup_value(raw, "Anonymous"),
                referenced_kb=self._parse_rollup_value(raw, "Referenced"),
                anon_hugepages_kb=self._parse_rollup_value(raw, "AnonHugePages"),
            )
        )

    def _emit_candidate(
        self,
        *,
        pid: int,
        ts_ns: int,
        candidate_type: str,
        start_addr: int,
        end_addr: int,
        referenced_kb: int,
        anon_hugepages_kb: int,
        thp_eligible: int,
        recent_scan_hit: int,
    ) -> str:
        key = (start_addr, end_addr)
        first_seen_ns = self.first_seen_ns.get(key, ts_ns)
        age_sec = float((ts_ns - first_seen_ns) / 1e9)
        self.decision_counter += 1
        decision_id = f"{self.collection_id}-cand-{self.decision_counter}"
        self.candidates.append(
            THPCandidateRow(
                decision_id=decision_id,
                pid=pid,
                tgid=pid,
                ts_ns=ts_ns,
                candidate_type=candidate_type,
                mm="",
                pfn=0,
                start_addr=start_addr,
                end_addr=end_addr,
                age_sec=age_sec,
                age_censored=True,
                referenced=referenced_kb,
                none_or_zero=0,
                writable=0,
                scan_status=0,
                anon_hugepages_kb=anon_hugepages_kb,
                thp_eligible=thp_eligible,
                recent_scan_hit=recent_scan_hit,
            )
        )
        return decision_id

    def _parse_smaps(self, pid: int, now_ns: int) -> list[SmapsVMARegionSample]:
        smaps_path = Path(f"/proc/{pid}/smaps")
        try:
            lines = smaps_path.read_text().splitlines()
        except Exception:
            return []

        samples = list[SmapsVMARegionSample]()
        current_start = 0
        current_end = 0
        rss_kb = 0
        anonymous_kb = 0
        referenced_kb = 0
        anon_hugepages_kb = 0
        thp_eligible = 0
        vm_flags = ""

        def maybe_emit():
            if current_start == 0 and current_end == 0:
                return
            if anon_hugepages_kb <= 0 and thp_eligible == 0:
                return
            samples.append(
                SmapsVMARegionSample(
                    pid=pid,
                    tgid=pid,
                    ts_ns=now_ns,
                    start_addr=current_start,
                    end_addr=current_end,
                    rss_kb=rss_kb,
                    anonymous_kb=anonymous_kb,
                    referenced_kb=referenced_kb,
                    anon_hugepages_kb=anon_hugepages_kb,
                    thp_eligible=thp_eligible,
                    vm_flags=vm_flags,
                )
            )

        for line in lines:
            if not line:
                continue
            if "-" in line and line[0].isalnum() and line[1].isalnum():
                maybe_emit()
                head = line.split(maxsplit=1)[0]
                start_raw, end_raw = head.split("-")
                current_start = int(start_raw, 16)
                current_end = int(end_raw, 16)
                rss_kb = 0
                anonymous_kb = 0
                referenced_kb = 0
                anon_hugepages_kb = 0
                thp_eligible = 0
                vm_flags = ""
                continue
            if line.startswith("Rss:"):
                rss_kb = int(line.split()[1])
            elif line.startswith("Anonymous:"):
                anonymous_kb = int(line.split()[1])
            elif line.startswith("Referenced:"):
                referenced_kb = int(line.split()[1])
            elif line.startswith("AnonHugePages:"):
                anon_hugepages_kb = int(line.split()[1])
            elif line.startswith("THPeligible:"):
                thp_eligible = int(line.split()[1])
            elif line.startswith("VmFlags:"):
                vm_flags = line.replace("VmFlags:", "").strip()
        maybe_emit()
        return samples

    def _select_stratified_region(
        self, regions: list[SmapsVMARegionSample], now_ns: int
    ) -> SmapsVMARegionSample | None:
        if not regions:
            return None
        by_bucket = {"young": [], "mid": [], "old": []}
        for region in regions:
            key = (region.start_addr, region.end_addr)
            first_seen = self.first_seen_ns.get(key, now_ns)
            age_sec = float((now_ns - first_seen) / 1e9)
            bucket = age_bucket_for(age_sec, censored=False)
            if bucket in by_bucket:
                by_bucket[bucket].append(region)
        weighted = []
        for bucket, weight in self.age_bucket_weights.items():
            if by_bucket.get(bucket):
                weighted.append((bucket, max(weight, 0.0)))
        if not weighted:
            return self.rand.choice(regions)
        pick = self.rand.random() * sum(weight for _, weight in weighted)
        run = 0.0
        chosen_bucket = weighted[-1][0]
        for bucket, weight in weighted:
            run += weight
            if pick <= run:
                chosen_bucket = bucket
                break
        return self.rand.choice(by_bucket[chosen_bucket])

    def _run_intervention(
        self,
        *,
        pid: int,
        now_ns: int,
        region: SmapsVMARegionSample,
        decision_id: str,
    ):
        start_hex = hex(region.start_addr)
        end_hex = hex(region.end_addr)
        command = f"{pid},{start_hex},{end_hex}"
        success = False
        error = ""
        try:
            self.split_debugfs_path.write_text(f"{command}\n")
            success = True
        except Exception as exc:
            error = str(exc)
        key = (region.start_addr, region.end_addr)
        first_seen = self.first_seen_ns.get(key, now_ns)
        age_sec = float((now_ns - first_seen) / 1e9)
        self.intervention_counter += 1
        self.interventions.append(
            THPInterventionRow(
                intervention_id=f"{self.collection_id}-iv-{self.intervention_counter}",
                decision_id=decision_id,
                pid=pid,
                tgid=pid,
                ts_ns=now_ns,
                start_addr=region.start_addr,
                end_addr=region.end_addr,
                age_bucket=age_bucket_for(age_sec, censored=False),
                success=success,
                error=error,
                command=command,
            )
        )

    def poll(self):
        now_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        pid = self._find_target_pid()
        if pid is None:
            return

        if now_ns - self.last_rollup_ns >= self.rollup_interval_ns:
            self.last_rollup_ns = now_ns
            self._sample_rollup(pid, now_ns)

        if now_ns - self.last_vma_ns < self.vma_interval_ns:
            return
        self.last_vma_ns = now_ns
        regions = self._parse_smaps(pid, now_ns)
        if not regions:
            return
        self.vma_samples.extend(regions)

        for region in regions:
            key = (region.start_addr, region.end_addr)
            if key not in self.first_seen_ns:
                self.first_seen_ns[key] = now_ns
            self.last_seen_ts[key] = now_ns

            prev_ref = self.last_seen_ref.get(key)
            self.last_seen_ref[key] = region.referenced_kb
            if prev_ref is None:
                continue
            if (
                abs(region.referenced_kb - prev_ref) <= 32
                and region.anon_hugepages_kb > 0
            ):
                if (
                    now_ns - self.last_negative_sample_ns
                    >= self.negative_sample_interval_ns
                ):
                    self.last_negative_sample_ns = now_ns
                    self._emit_candidate(
                        pid=pid,
                        ts_ns=now_ns,
                        candidate_type="negative",
                        start_addr=region.start_addr,
                        end_addr=region.end_addr,
                        referenced_kb=region.referenced_kb,
                        anon_hugepages_kb=region.anon_hugepages_kb,
                        thp_eligible=region.thp_eligible,
                        recent_scan_hit=0,
                    )

        sampled = self.rand.choice(regions)
        decision_id = self._emit_candidate(
            pid=pid,
            ts_ns=now_ns,
            candidate_type="sampled",
            start_addr=sampled.start_addr,
            end_addr=sampled.end_addr,
            referenced_kb=sampled.referenced_kb,
            anon_hugepages_kb=sampled.anon_hugepages_kb,
            thp_eligible=sampled.thp_eligible,
            recent_scan_hit=0,
        )

        if not self.intervention_enabled:
            return
        if now_ns - self.started_ns < self.intervention_warmup_ns:
            return
        if now_ns - self.last_intervention_ns < self.intervention_period_ns:
            return
        self.last_intervention_ns = now_ns
        region = self._select_stratified_region(regions, now_ns)
        if region is None:
            return
        self._run_intervention(
            pid=pid, now_ns=now_ns, region=region, decision_id=decision_id
        )

    def close(self):
        pass

    def data(self) -> list[CollectionTable]:
        tables: list[CollectionTable] = []
        if self.rollup_samples:
            tables.append(
                SmapsRollupSampleTable.from_df_id(
                    pl.DataFrame(self.rollup_samples), collection_id=self.collection_id
                )
            )
        if self.vma_samples:
            tables.append(
                SmapsVMARegionSampleTable.from_df_id(
                    pl.DataFrame(self.vma_samples), collection_id=self.collection_id
                )
            )
        if self.candidates:
            tables.append(
                THPCandidateTable.from_df_id(
                    pl.DataFrame(self.candidates), collection_id=self.collection_id
                )
            )
        if self.interventions:
            tables.append(
                THPInterventionEventTable.from_df_id(
                    pl.DataFrame(self.interventions), collection_id=self.collection_id
                )
            )
        return tables

    def clear(self):
        self.rollup_samples.clear()
        self.vma_samples.clear()
        self.candidates.clear()
        self.interventions.clear()

    def pop_data(self) -> list[CollectionTable]:
        tables = self.data()
        self.clear()
        return tables

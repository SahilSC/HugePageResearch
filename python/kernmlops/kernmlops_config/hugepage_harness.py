from dataclasses import dataclass, field
from kernmlops_config import ConfigBase

@dataclass(frozen=True)
class HugepageHarnessConfig(ConfigBase):
    redis_name_regex: str = "redis-server"
    smaps_rollup_interval_ms: int = 500
    smaps_vma_interval_ms: int = 2000
    vmstat_interval_ms: int = 200
    negative_sample_period_s: int = 2
    intervention_enabled: bool = True
    intervention_period_s: int = 10
    intervention_warmup_s: int = 30
    split_debugfs_path: str = "/sys/kernel/debug/split_huge_pages"
    age_bucket_weights: list[float] = field(default_factory=lambda: [0.4, 0.4, 0.2])
    random_seed: int = 17

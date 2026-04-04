from dataclasses import field, make_dataclass

from data_collection import CollectorConfig
from kernmlops_benchmark import BenchmarkConfig
from kernmlops_config import ConfigBase
from kernmlops_config.hugepage_harness import HugepageHarnessConfig

from kernmlops_config.hugepage_harness import HugepageHarnessConfig

KernmlopsConfig = make_dataclass(
    cls_name="KernmlopsConfig",
    bases=(ConfigBase,),
    fields=[
        (
            "benchmark_config",
            BenchmarkConfig,
            field(default=BenchmarkConfig()),
        ),
        (
            "collector_config",
            CollectorConfig,
            field(default=CollectorConfig()),
        ),
        (
            "hugepage_harness",
            HugepageHarnessConfig,
            field(default=HugepageHarnessConfig()),
        ),
    ],
    frozen=True,
)


__all__ = [
    "KernmlopsConfig",
]

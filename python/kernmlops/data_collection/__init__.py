from dataclasses import dataclass, field, make_dataclass
from pathlib import Path

from data_collection import bpf_instrumentation as bpf
from data_collection.system_info import machine_info
from kernmlops_config import ConfigBase

@dataclass(frozen=True)
class GenericCollectorConfig(ConfigBase):
    poll_rate: float = 0.5
    output_interval: str = "1m"
    output_dir: str = "data"
    output_dfs: bool = False
    output_graphs: bool = False
    hooks: list[str] = field(default_factory=bpf.hook_names)

    def get_output_dir(self) -> Path:
        return Path(self.output_dir)

    def get_hooks(self, hugepage_harness: ConfigBase | None = None) -> list[bpf.BPFProgram]:
        hooks = []
        for hook_name in self.hooks:
            hook_type = bpf.all_hooks.get(hook_name)
            if hook_type is None:
                raise ValueError("Hook_name: ", hook_name, "Not found. Ignoring hook.")
            if hook_name in ["smaps_harness", "vmstat_harness"]:
                hooks.append(hook_type(hugepage_harness=hugepage_harness))
            else:
                hooks.append(hook_type())
        return hooks


CollectorConfig = make_dataclass(
    cls_name="CollectorConfig",
    bases=(ConfigBase,),
    fields=[
        (
            "generic",
            GenericCollectorConfig,
            field(default=GenericCollectorConfig()),
        )
    ],
    frozen=True,
)


__all__ = [
    "bpf",
    "machine_info",
    "CollectorConfig",
    "GenericCollectorConfig",
]

from dataclasses import dataclass, field, make_dataclass
from pathlib import Path
from typing import Any

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

    def get_hooks(
        self,
        hugepage_harness: ConfigBase | None = None,
        benchmark: Any | None = None,
    ) -> list[bpf.BPFProgram]:
        hooks = []
        for hook_name in self.hooks:
            hook_type = bpf.all_hooks.get(hook_name)
            if hook_type is None:
                raise ValueError("Hook_name: ", hook_name, "Not found. Ignoring hook.")
            if hook_name in ["thp_intervention", "vmstat_harness"]:
                hooks.append(hook_type(hugepage_harness=hugepage_harness))
            elif hook_name == "vaptr" and benchmark is not None:

                num_keys = getattr(
                    getattr(benchmark, "config", None), "vaptr_num_keys", 10
                )
                field_name = getattr(
                    getattr(benchmark, "config", None), "vaptr_field_name", "field0"
                )
                hooks.append(hook_type(num_keys=num_keys, field_name=field_name))
            elif hook_name in ["proc_maps", "smaps_hook"] and benchmark is not None:
                process_name = getattr(
                    benchmark,
                    "redis_server_name",
                    lambda: benchmark.name(),
                )()
                hooks.append(hook_type(process_name=process_name))
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

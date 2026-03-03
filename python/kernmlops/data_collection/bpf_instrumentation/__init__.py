"""Module for maintaining different BPF hooks/instrumentation."""

from typing import Final, Mapping

from data_collection.bpf_instrumentation.blk_io_hook import BlockIOBPFHook
from data_collection.bpf_instrumentation.bpf_hook import BPFProgram
from data_collection.bpf_instrumentation.cbmm import (
    CBMMBPFHook,
)
from data_collection.bpf_instrumentation.collapse_huge_page import (
    CollapseHugePageBPFHook,
)
from data_collection.bpf_instrumentation.file_data_hook import FileDataBPFHook
from data_collection.bpf_instrumentation.fork_and_exit import TraceProcessHook
from data_collection.bpf_instrumentation.madvise import MadviseBPFHook
from data_collection.bpf_instrumentation.memory_usage_hook import MemoryUsageHook
from data_collection.bpf_instrumentation.mm_rss_stat import TraceRSSStatBPFHook
from data_collection.bpf_instrumentation.perf import (
    CustomHWConfigManager,
    PerfBPFHook,
)
from data_collection.bpf_instrumentation.proc_maps_hook import ProcMapsHook
from data_collection.bpf_instrumentation.process_metadata_hook import (
    ProcessMetadataHook,
)
from data_collection.bpf_instrumentation.quanta_runtime_hook import QuantaRuntimeBPFHook
from data_collection.bpf_instrumentation.proc_smaps_hook import ProcSmapsHook
from data_collection.bpf_instrumentation.thp_intervention_hook import THPInterventionHook
from data_collection.bpf_instrumentation.unmap_range import UnmapRangeBPFHook
from data_collection.bpf_instrumentation.vaptr_hook import VAPtrHook
from data_collection.bpf_instrumentation.vmstat_harness import VMStatHarnessHook
from data_collection.bpf_instrumentation.zswap_runtime_hook import ZswapRuntimeBPFHook

all_hooks: Final[Mapping[str, type[BPFProgram]]] = {
    FileDataBPFHook.name(): FileDataBPFHook,
    MemoryUsageHook.name(): MemoryUsageHook,
    ProcessMetadataHook.name(): ProcessMetadataHook,
    QuantaRuntimeBPFHook.name(): QuantaRuntimeBPFHook,
    BlockIOBPFHook.name(): BlockIOBPFHook,
    PerfBPFHook.name(): PerfBPFHook,
    CollapseHugePageBPFHook.name(): CollapseHugePageBPFHook,
    CBMMBPFHook.name(): CBMMBPFHook,
    MadviseBPFHook.name(): MadviseBPFHook,
    UnmapRangeBPFHook.name(): UnmapRangeBPFHook,
    TraceRSSStatBPFHook.name(): TraceRSSStatBPFHook,
    TraceProcessHook.name(): TraceProcessHook,
    ZswapRuntimeBPFHook.name(): ZswapRuntimeBPFHook,
    ProcSmapsHook.name(): ProcSmapsHook,
    THPInterventionHook.name(): THPInterventionHook,
    VMStatHarnessHook.name(): VMStatHarnessHook,
    ProcMapsHook.name(): ProcMapsHook,
    VAPtrHook.name(): VAPtrHook,
}


def hook_names() -> list[str]:
    return list(all_hooks.keys())


__all__ = [
    "all_hooks",
    "hook_names",
    "BPFProgram",
    "CustomHWConfigManager",
    "QuantaRuntimeBPFHook",
]

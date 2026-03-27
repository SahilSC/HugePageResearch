"""Module for maintaining different BPF hooks/instrumentation."""

from importlib import import_module
from typing import Final

from data_collection.bpf_instrumentation.bpf_hook import BPFProgram

_HOOK_REGISTRY: Final[dict[str, tuple[str, str]]] = {
    "file_data": (
        "data_collection.bpf_instrumentation.file_data_hook",
        "FileDataBPFHook",
    ),
    "memory_usage": (
        "data_collection.bpf_instrumentation.memory_usage_hook",
        "MemoryUsageHook",
    ),
    "process_metadata": (
        "data_collection.bpf_instrumentation.process_metadata_hook",
        "ProcessMetadataHook",
    ),
    "quanta_runtime": (
        "data_collection.bpf_instrumentation.quanta_runtime_hook",
        "QuantaRuntimeBPFHook",
    ),
    "block_io": (
        "data_collection.bpf_instrumentation.blk_io_hook",
        "BlockIOBPFHook",
    ),
    "perf": (
        "data_collection.bpf_instrumentation.perf.perf_hook",
        "PerfBPFHook",
    ),
    "collapse_huge_pages": (
        "data_collection.bpf_instrumentation.collapse_huge_page",
        "CollapseHugePageBPFHook",
    ),
    "cbmm": (
        "data_collection.bpf_instrumentation.cbmm",
        "CBMMBPFHook",
    ),
    "madvise": (
        "data_collection.bpf_instrumentation.madvise",
        "MadviseBPFHook",
    ),
    "unmap_range": (
        "data_collection.bpf_instrumentation.unmap_range",
        "UnmapRangeBPFHook",
    ),
    "mm_rss_stat": (
        "data_collection.bpf_instrumentation.mm_rss_stat",
        "TraceRSSStatBPFHook",
    ),
    "process_trace": (
        "data_collection.bpf_instrumentation.fork_and_exit",
        "TraceProcessHook",
    ),
    "zswap_runtime": (
        "data_collection.bpf_instrumentation.zswap_runtime_hook",
        "ZswapRuntimeBPFHook",
    ),
    "smaps_hook": (
        "data_collection.bpf_instrumentation.proc_smaps_hook",
        "ProcSmapsHook",
    ),
    "thp_intervention": (
        "data_collection.bpf_instrumentation.thp_intervention_hook",
        "THPInterventionHook",
    ),
    "vmstat_harness": (
        "data_collection.bpf_instrumentation.vmstat_harness",
        "VMStatHarnessHook",
    ),
    "proc_maps": (
        "data_collection.bpf_instrumentation.proc_maps_hook",
        "ProcMapsHook",
    ),
    "vaptr": (
        "data_collection.bpf_instrumentation.vaptr_hook",
        "VAPtrHook",
    ),
}

_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "CustomHWConfigManager": (
        "data_collection.bpf_instrumentation.perf.perf_config",
        "CustomHWConfigManager",
    ),
    "QuantaRuntimeBPFHook": (
        "data_collection.bpf_instrumentation.quanta_runtime_hook",
        "QuantaRuntimeBPFHook",
    ),
}


def _load_attr(module_path: str, attr_name: str):
    module = import_module(module_path)
    return getattr(module, attr_name)


def get_hook(hook_name: str) -> type[BPFProgram] | None:
    target = _HOOK_REGISTRY.get(hook_name)
    if target is None:
        return None
    return _load_attr(*target)


def hook_names() -> list[str]:
    return list(_HOOK_REGISTRY.keys())


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return _load_attr(*target)


__all__ = [
    "get_hook",
    "hook_names",
    "BPFProgram",
    "CustomHWConfigManager",
    "QuantaRuntimeBPFHook",
]

"""Module for maintaining different BPF hooks/instrumentation."""

from typing import Final, Mapping

from data_collection.bpf_instrumentation.bpf_hook import BPFProgram

_HOOK_NAMES: Final[tuple[str, ...]] = (
    "file_data",
    "memory_usage",
    "process_metadata",
    "quanta_runtime",
    "block_io",
    "perf",
    "collapse_huge_pages",
    "cbmm",
    "madvise",
    "unmap_range",
    "mm_rss_stat",
    "process_trace",
    "zswap_runtime",
    "smaps_hook",
    "thp_intervention",
    "vmstat_harness",
    "proc_maps",
    "vaptr",
)


def _build_all_hooks() -> Mapping[str, type[BPFProgram]]:
    return {hook_name: get_hook(hook_name) for hook_name in _HOOK_NAMES}


def get_hook(hook_name: str) -> type[BPFProgram] | None:
    match hook_name:
        case "file_data":
            from data_collection.bpf_instrumentation.file_data_hook import (
                FileDataBPFHook,
            )

            return FileDataBPFHook
        case "memory_usage":
            from data_collection.bpf_instrumentation.memory_usage_hook import (
                MemoryUsageHook,
            )

            return MemoryUsageHook
        case "process_metadata":
            from data_collection.bpf_instrumentation.process_metadata_hook import (
                ProcessMetadataHook,
            )

            return ProcessMetadataHook
        case "quanta_runtime":
            from data_collection.bpf_instrumentation.quanta_runtime_hook import (
                QuantaRuntimeBPFHook,
            )

            return QuantaRuntimeBPFHook
        case "block_io":
            from data_collection.bpf_instrumentation.blk_io_hook import BlockIOBPFHook

            return BlockIOBPFHook
        case "perf":
            from data_collection.bpf_instrumentation.perf import PerfBPFHook

            return PerfBPFHook
        case "collapse_huge_pages":
            from data_collection.bpf_instrumentation.collapse_huge_page import (
                CollapseHugePageBPFHook,
            )

            return CollapseHugePageBPFHook
        case "cbmm":
            from data_collection.bpf_instrumentation.cbmm import CBMMBPFHook

            return CBMMBPFHook
        case "madvise":
            from data_collection.bpf_instrumentation.madvise import MadviseBPFHook

            return MadviseBPFHook
        case "unmap_range":
            from data_collection.bpf_instrumentation.unmap_range import (
                UnmapRangeBPFHook,
            )

            return UnmapRangeBPFHook
        case "mm_rss_stat":
            from data_collection.bpf_instrumentation.mm_rss_stat import (
                TraceRSSStatBPFHook,
            )

            return TraceRSSStatBPFHook
        case "process_trace":
            from data_collection.bpf_instrumentation.fork_and_exit import (
                TraceProcessHook,
            )

            return TraceProcessHook
        case "zswap_runtime":
            from data_collection.bpf_instrumentation.zswap_runtime_hook import (
                ZswapRuntimeBPFHook,
            )

            return ZswapRuntimeBPFHook
        case "smaps_hook":
            from data_collection.bpf_instrumentation.proc_smaps_hook import (
                ProcSmapsHook,
            )

            return ProcSmapsHook
        case "thp_intervention":
            from data_collection.bpf_instrumentation.thp_intervention_hook import (
                THPInterventionHook,
            )

            return THPInterventionHook
        case "vmstat_harness":
            from data_collection.bpf_instrumentation.vmstat_harness import (
                VMStatHarnessHook,
            )

            return VMStatHarnessHook
        case "proc_maps":
            from data_collection.bpf_instrumentation.proc_maps_hook import ProcMapsHook

            return ProcMapsHook
        case "vaptr":
            from data_collection.bpf_instrumentation.vaptr_hook import VAPtrHook

            return VAPtrHook
        case _:
            return None


def hook_names() -> list[str]:
    return list(_HOOK_NAMES)


def __getattr__(name: str):
    if name == "all_hooks":
        return _build_all_hooks()
    if name == "CustomHWConfigManager":
        from data_collection.bpf_instrumentation.perf.perf_config import (
            CustomHWConfigManager,
        )

        return CustomHWConfigManager
    if name == "QuantaRuntimeBPFHook":
        hook_type = get_hook("quanta_runtime")
        if hook_type is not None:
            return hook_type
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "all_hooks",
    "get_hook",
    "hook_names",
    "BPFProgram",
    "CustomHWConfigManager",
    "QuantaRuntimeBPFHook",
]

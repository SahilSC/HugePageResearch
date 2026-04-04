from typing import Mapping

from data_schema.perf.perf_schema import (
    CustomHWEventID,
    PerfCollectionTable,
)
from data_schema.perf.tlb_perf import (
    DTLBPerfTable,
    DTLBLoadsPerfTable,
    DTLBWalkDurationPerfTable,
    ITLBLoadsPerfTable,
    ITLBPerfTable,
    ITLBWalkDurationPerfTable,
    TLBFlushPerfTable,
)
from data_schema.perf.faults_perf import (
    PageFaultsPerfTable,
    MinorFaultsPerfTable,
    MajorFaultsPerfTable,
)
from data_schema.perf.instructions_perf import InstructionsPerfTable


perf_table_types: Mapping[str, type[PerfCollectionTable]] = {
    DTLBPerfTable.name(): DTLBPerfTable,
    DTLBLoadsPerfTable.name(): DTLBLoadsPerfTable,
    ITLBPerfTable.name(): ITLBPerfTable,
    ITLBLoadsPerfTable.name(): ITLBLoadsPerfTable,
    TLBFlushPerfTable.name(): TLBFlushPerfTable,
    DTLBWalkDurationPerfTable.name(): DTLBWalkDurationPerfTable,
    ITLBWalkDurationPerfTable.name(): ITLBWalkDurationPerfTable,
    PageFaultsPerfTable.name(): PageFaultsPerfTable,
    MinorFaultsPerfTable.name(): MinorFaultsPerfTable,
    MajorFaultsPerfTable.name(): MajorFaultsPerfTable,
    InstructionsPerfTable.name(): InstructionsPerfTable,
}

__all__ = [
    "perf_table_types",
    "CustomHWEventID",
    "PerfCollectionTable",
]

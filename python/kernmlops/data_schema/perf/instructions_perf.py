import polars as pl
from bcc import PerfHWConfig, PerfType
from data_schema.memory_usage import MemoryUsageGraph
from data_schema.perf.perf_schema import (
    CustomHWEventID,
    PerfCollectionTable,
    RatePerfGraph,
)
from data_schema.schema import (
    CollectionGraph,
    GraphEngine,
)


class InstructionsPerfTable(PerfCollectionTable):
    @classmethod
    def name(cls) -> str:
        return "instructions_retired"

    @classmethod
    def ev_type(cls) -> int:
        return PerfType.HARDWARE

    @classmethod
    def ev_config(cls) -> int:
        return PerfHWConfig.INSTRUCTIONS

    @classmethod
    def hw_ids(cls) -> list[CustomHWEventID]:
        return []

    @classmethod
    def component_name(cls) -> str:
        return "CPU"

    @classmethod
    def measured_event_name(cls) -> str:
        return "Instructions"

    @classmethod
    def from_df(cls, table: pl.DataFrame) -> "InstructionsPerfTable":
        return InstructionsPerfTable(table=table.cast(cls.schema(), strict=True))  # pyright: ignore [reportArgumentType]

    def __init__(self, table: pl.DataFrame):
        self._table = table

    @property
    def table(self) -> pl.DataFrame:
        return self._table

    def filtered_table(self) -> pl.DataFrame:
        return self.table

    def graphs(self) -> list[type[CollectionGraph]]:
        return [InstructionsRateGraph]


class InstructionsRateGraph(RatePerfGraph):
    @classmethod
    def perf_table_type(cls) -> type[PerfCollectionTable]:
        return InstructionsPerfTable

    @classmethod
    def trend_graph(cls) -> type[CollectionGraph] | None:
        return MemoryUsageGraph

    @classmethod
    def with_graph_engine(cls, graph_engine: GraphEngine) -> CollectionGraph | None:
        perf_table = graph_engine.collection_data.get(cls.perf_table_type())
        if perf_table is not None:
            return InstructionsRateGraph(graph_engine=graph_engine, perf_table=perf_table)
        return None

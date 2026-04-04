import polars as pl
from bcc import PerfSWConfig, PerfType
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


class PageFaultsPerfTable(PerfCollectionTable):
    @classmethod
    def name(cls) -> str:
        return "page_faults"

    @classmethod
    def ev_type(cls) -> int:
        return PerfType.SOFTWARE

    @classmethod
    def ev_config(cls) -> int:
        return PerfSWConfig.PAGE_FAULTS

    @classmethod
    def hw_ids(cls) -> list[CustomHWEventID]:
        return []

    @classmethod
    def component_name(cls) -> str:
        return "System"

    @classmethod
    def measured_event_name(cls) -> str:
        return "Page Faults"

    @classmethod
    def from_df(cls, table: pl.DataFrame) -> "PageFaultsPerfTable":
        return PageFaultsPerfTable(table=table.cast(cls.schema(), strict=True))  # pyright: ignore [reportArgumentType]

    def __init__(self, table: pl.DataFrame):
        self._table = table

    @property
    def table(self) -> pl.DataFrame:
        return self._table

    def filtered_table(self) -> pl.DataFrame:
        return self.table

    def graphs(self) -> list[type[CollectionGraph]]:
        return [PageFaultsRateGraph]


class PageFaultsRateGraph(RatePerfGraph):
    @classmethod
    def perf_table_type(cls) -> type[PerfCollectionTable]:
        return PageFaultsPerfTable

    @classmethod
    def trend_graph(cls) -> type[CollectionGraph] | None:
        return MemoryUsageGraph

    @classmethod
    def with_graph_engine(cls, graph_engine: GraphEngine) -> CollectionGraph | None:
        perf_table = graph_engine.collection_data.get(cls.perf_table_type())
        if perf_table is not None:
            return PageFaultsRateGraph(graph_engine=graph_engine, perf_table=perf_table)
        return None


class MinorFaultsPerfTable(PerfCollectionTable):
    @classmethod
    def name(cls) -> str:
        return "minor_faults"

    @classmethod
    def ev_type(cls) -> int:
        return PerfType.SOFTWARE

    @classmethod
    def ev_config(cls) -> int:
        return PerfSWConfig.PAGE_FAULTS_MIN

    @classmethod
    def hw_ids(cls) -> list[CustomHWEventID]:
        return []

    @classmethod
    def component_name(cls) -> str:
        return "System"

    @classmethod
    def measured_event_name(cls) -> str:
        return "Minor Faults"

    @classmethod
    def from_df(cls, table: pl.DataFrame) -> "MinorFaultsPerfTable":
        return MinorFaultsPerfTable(table=table.cast(cls.schema(), strict=True))  # pyright: ignore [reportArgumentType]

    def __init__(self, table: pl.DataFrame):
        self._table = table

    @property
    def table(self) -> pl.DataFrame:
        return self._table

    def filtered_table(self) -> pl.DataFrame:
        return self.table

    def graphs(self) -> list[type[CollectionGraph]]:
        return [MinorFaultsRateGraph]


class MinorFaultsRateGraph(RatePerfGraph):
    @classmethod
    def perf_table_type(cls) -> type[PerfCollectionTable]:
        return MinorFaultsPerfTable

    @classmethod
    def with_graph_engine(cls, graph_engine: GraphEngine) -> CollectionGraph | None:
        perf_table = graph_engine.collection_data.get(cls.perf_table_type())
        if perf_table is not None:
            return MinorFaultsRateGraph(graph_engine=graph_engine, perf_table=perf_table)
        return None


class MajorFaultsPerfTable(PerfCollectionTable):
    @classmethod
    def name(cls) -> str:
        return "major_faults"

    @classmethod
    def ev_type(cls) -> int:
        return PerfType.SOFTWARE

    @classmethod
    def ev_config(cls) -> int:
        return PerfSWConfig.PAGE_FAULTS_MAJ

    @classmethod
    def hw_ids(cls) -> list[CustomHWEventID]:
        return []

    @classmethod
    def component_name(cls) -> str:
        return "System"

    @classmethod
    def measured_event_name(cls) -> str:
        return "Major Faults"

    @classmethod
    def from_df(cls, table: pl.DataFrame) -> "MajorFaultsPerfTable":
        return MajorFaultsPerfTable(table=table.cast(cls.schema(), strict=True))  # pyright: ignore [reportArgumentType]

    def __init__(self, table: pl.DataFrame):
        self._table = table

    @property
    def table(self) -> pl.DataFrame:
        return self._table

    def filtered_table(self) -> pl.DataFrame:
        return self.table

    def graphs(self) -> list[type[CollectionGraph]]:
        return [MajorFaultsRateGraph]


class MajorFaultsRateGraph(RatePerfGraph):
    @classmethod
    def perf_table_type(cls) -> type[PerfCollectionTable]:
        return MajorFaultsPerfTable

    @classmethod
    def with_graph_engine(cls, graph_engine: GraphEngine) -> CollectionGraph | None:
        perf_table = graph_engine.collection_data.get(cls.perf_table_type())
        if perf_table is not None:
            return MajorFaultsRateGraph(graph_engine=graph_engine, perf_table=perf_table)
        return None

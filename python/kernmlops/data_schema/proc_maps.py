import polars as pl
from data_schema.schema import (
    UPTIME_TIMESTAMP,
    CollectionGraph,
    CollectionTable,
)


class ProcMapsTable(CollectionTable):
    @classmethod
    def name(cls) -> str:
        return "proc_maps"

    @classmethod
    def schema(cls) -> pl.Schema:
        return pl.Schema(
            {
                "pid": pl.Int64(),
                "tgid": pl.Int64(),
                UPTIME_TIMESTAMP: pl.Int64(),
                "start_addr": pl.UInt64(),
                "end_addr": pl.UInt64(),
                "size_kb": pl.UInt64(),
                "perms": pl.String(),
                "offset": pl.UInt64(),
                "dev": pl.String(),
                "inode": pl.UInt64(),
                "pathname": pl.String(),
            }
        )

    @classmethod
    def from_df(cls, table: pl.DataFrame) -> "ProcMapsTable":
        return ProcMapsTable(table=table)

    def __init__(self, table: pl.DataFrame):
        self._table = table

    @property
    def table(self) -> pl.DataFrame:
        return self._table

    def filtered_table(self) -> pl.DataFrame:
        return self.table

    def graphs(self) -> list[type[CollectionGraph]]:
        return []

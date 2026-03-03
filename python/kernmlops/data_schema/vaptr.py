import polars as pl
from data_schema.schema import (
    UPTIME_TIMESTAMP,
    CollectionGraph,
    CollectionTable,
)


class VAPtrTable(CollectionTable):
    @classmethod
    def name(cls) -> str:
        return "vaptr"

    @classmethod
    def schema(cls) -> pl.Schema:
        return pl.Schema(
            {
                UPTIME_TIMESTAMP: pl.Int64(),
                "key": pl.String(),
                "address": pl.String(),
                "page_addr": pl.String(),
            }
        )

    @classmethod
    def from_df(cls, table: pl.DataFrame) -> "VAPtrTable":
        return VAPtrTable(table=table)

    def __init__(self, table: pl.DataFrame):
        self._table = table

    @property
    def table(self) -> pl.DataFrame:
        return self._table

    def filtered_table(self) -> pl.DataFrame:
        return self.table

    def graphs(self) -> list[type[CollectionGraph]]:
        return []

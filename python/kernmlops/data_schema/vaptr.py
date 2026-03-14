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
                "available": pl.Boolean(),
                "mapped_pfn": pl.UInt64(),
                "tracking_pfn": pl.UInt64(),
                "physical_page_addr": pl.UInt64(),
                "tracking_physical_page_addr": pl.UInt64(),
                "page_idle": pl.Boolean(),
                "access_bit": pl.Boolean(),
                "access_bit_valid": pl.Boolean(),
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

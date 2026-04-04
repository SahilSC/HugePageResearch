import polars as pl
from data_schema.schema import (
    UPTIME_TIMESTAMP,
    CollectionGraph,
    CollectionTable,
)


class ProcSmapsTable(CollectionTable):
    @classmethod
    def name(cls) -> str:
        return "proc_smaps"

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
                "kernel_page_size_kb": pl.Int64(),
                "mmu_page_size_kb": pl.Int64(),
                "rss_kb": pl.Int64(),
                "pss_kb": pl.Int64(),
                "pss_dirty_kb": pl.Int64(),
                "shared_clean_kb": pl.Int64(),
                "shared_dirty_kb": pl.Int64(),
                "private_clean_kb": pl.Int64(),
                "private_dirty_kb": pl.Int64(),
                "referenced_kb": pl.Int64(),
                "anonymous_kb": pl.Int64(),
                "ksm_kb": pl.Int64(),
                "lazy_free_kb": pl.Int64(),
                "anon_hugepages_kb": pl.Int64(),
                "shmem_pmd_mapped_kb": pl.Int64(),
                "file_pmd_mapped_kb": pl.Int64(),
                "shared_hugetlb_kb": pl.Int64(),
                "private_hugetlb_kb": pl.Int64(),
                "swap_kb": pl.Int64(),
                "swap_pss_kb": pl.Int64(),
                "locked_kb": pl.Int64(),
                "thp_eligible": pl.Int64(),
                "protection_key": pl.Int64(),
                "vm_flags": pl.String(),
            }
        )

    @classmethod
    def from_df(cls, table: pl.DataFrame) -> "ProcSmapsTable":
        return ProcSmapsTable(table=table)

    def __init__(self, table: pl.DataFrame):
        self._table = table

    @property
    def table(self) -> pl.DataFrame:
        return self._table

    def filtered_table(self) -> pl.DataFrame:
        return self.table

    def graphs(self) -> list[type[CollectionGraph]]:
        return []

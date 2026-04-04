# ruff: noqa: E402
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "kernmlops"))

from data_collection.system_info import machine_info
from data_schema.proc_maps import ProcMapsTable
from data_schema.schema import CollectionData, SystemInfoTable


class CollectionDataLayoutTest(unittest.TestCase):
    def test_from_data_loads_nested_run_layout_and_merges_chunks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir)
            run_dir = data_dir / "redis" / "run-abc"
            run_dir.mkdir(parents=True)

            system_info = machine_info().to_polars()
            system_info = system_info.unnest(system_info.columns)
            system_info.write_parquet(run_dir / "system_info.end.parquet")

            proc_maps_schema = ProcMapsTable.schema()
            chunk_a = pl.DataFrame(
                {
                    "pid": [1],
                    "tgid": [1],
                    "ts_uptime_us": [10],
                    "start_addr": [0x1000],
                    "end_addr": [0x2000],
                    "size_kb": [4],
                    "perms": ["rw-p"],
                    "offset": [0],
                    "dev": ["00:00"],
                    "inode": [0],
                    "pathname": [""],
                },
                schema=proc_maps_schema,
            )
            chunk_b = pl.DataFrame(
                {
                    "pid": [1],
                    "tgid": [1],
                    "ts_uptime_us": [20],
                    "start_addr": [0x3000],
                    "end_addr": [0x4000],
                    "size_kb": [4],
                    "perms": ["rw-p"],
                    "offset": [0],
                    "dev": ["00:00"],
                    "inode": [0],
                    "pathname": [""],
                },
                schema=proc_maps_schema,
            )
            chunk_a.write_parquet(run_dir / "proc_maps.0.parquet")
            chunk_b.write_parquet(run_dir / "proc_maps.end.parquet")

            collection = CollectionData.from_data(
                data_dir=data_dir,
                collection_id="run-abc",
                table_types=[SystemInfoTable, ProcMapsTable],
            )

            proc_maps = collection.get(ProcMapsTable)
            self.assertIsNotNone(proc_maps)
            assert proc_maps is not None
            self.assertEqual(len(proc_maps.table), 2)
            self.assertEqual(
                proc_maps.table["collection_id"].unique().to_list(),
                ["run-abc"],
            )


if __name__ == "__main__":
    unittest.main()

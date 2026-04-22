"""Generate deterministic GUPS breakpoint matrices from page-summary CSVs.

The input CSV comes from the seeded GUPS calibration pass and contains one row
per 2 MiB table region per repeat. This script validates that the page summary
is stable across repeats, then emits a Redis-style breakpoint matrix:

1. ``base_pages`` sentinel row
2. ``no_break`` intact-THP baseline
3. split-only hottest-page rows
4. split-only random-page rows
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path

import polars as pl


@dataclass(frozen=True)
class PageSummary:
    """Stable calibration summary for one 2 MiB GUPS table region.

    Attributes:
        page_index: Table-local 2 MiB region index.
        update_count: Timed updates that touched this page in one repeat.
        first_touch_update: Global update index of the page's first touch.
        last_touch_update: Global update index of the page's last touch.
    """

    page_index: int
    update_count: int
    first_touch_update: int
    last_touch_update: int


def breakpoint_column_name(page_index: int) -> str:
    """Return the stable Parquet column name for one page index."""
    return f"hp_{page_index:06d}"


def load_page_summaries(summary_csv: Path) -> list[PageSummary]:
    """Load and validate one deterministic GUPS calibration summary.

    Example output:
        [PageSummary(page_index=7, update_count=123, ...)]

    Args:
        summary_csv: Path to the GUPS ``--page-summary-out`` CSV.

    Returns:
        Stable per-page summaries ordered by descending ``update_count`` and
        then ascending ``page_index``.

    Raises:
        FileNotFoundError: If the CSV does not exist.
        RuntimeError: If repeats disagree about one page's counts.
    """
    if not summary_csv.is_file():
        raise FileNotFoundError(f"GUPS page summary not found: {summary_csv}")

    df = pl.read_csv(summary_csv)
    required_columns = {
        "repeat_index",
        "page_index",
        "update_count",
        "first_touch_update",
        "last_touch_update",
    }
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise RuntimeError(
            f"GUPS page summary is missing required columns: {missing_text}"
        )

    stable = (
        df.group_by("page_index")
        .agg(
            pl.col("update_count").n_unique().alias("update_count_variants"),
            pl.col("first_touch_update").n_unique().alias("first_touch_variants"),
            pl.col("last_touch_update").n_unique().alias("last_touch_variants"),
            pl.first("update_count").alias("update_count"),
            pl.first("first_touch_update").alias("first_touch_update"),
            pl.first("last_touch_update").alias("last_touch_update"),
        )
        .sort(
            by=["update_count", "page_index"],
            descending=[True, False],
        )
    )

    unstable = stable.filter(
        (pl.col("update_count_variants") > 1)
        | (pl.col("first_touch_variants") > 1)
        | (pl.col("last_touch_variants") > 1)
    )
    if unstable.height > 0:
        raise RuntimeError(
            "GUPS calibration summary is not deterministic across repeats; "
            "rerun calibration with --threads 1 and a fixed --stream-seed."
        )

    return [
        PageSummary(
            page_index=int(row["page_index"]),
            update_count=int(row["update_count"]),
            first_touch_update=int(row["first_touch_update"]),
            last_touch_update=int(row["last_touch_update"]),
        )
        for row in stable.iter_rows(named=True)
        if int(row["update_count"]) > 0
    ]


def generate_breakpoint_rows(
    page_summaries: list[PageSummary],
    *,
    hot_pages: int = 10,
    random_rows: int = 3,
    random_seed: int = 0,
) -> list[dict[str, int | str]]:
    """Build a Redis-style breakpoint matrix for deterministic GUPS.

    Args:
        page_summaries: Stable per-page calibration summaries.
        hot_pages: Number of hottest split-only rows to emit.
        random_rows: Number of random split-only rows to emit from the
            remaining pages.
        random_seed: Stable RNG seed for random-page row selection.

    Returns:
        Ordered row dictionaries ready to write to Parquet.
    """
    if not page_summaries:
        return [
            {
                "row_kind": "base_pages",
                "row_label": "base_pages",
                "target_page_index": -1,
                "target_column": "",
                "target_update_count": 0,
                "target_break_after_page_accesses": -1,
            }
        ]

    hottest = page_summaries[:hot_pages]
    remaining = page_summaries[hot_pages:]
    rng = random.Random(random_seed)
    random_pages = rng.sample(remaining, k=min(random_rows, len(remaining)))
    selected_pages = hottest + random_pages
    page_columns = [breakpoint_column_name(page.page_index) for page in selected_pages]
    max_values = {
        breakpoint_column_name(page.page_index): page.update_count + 1
        for page in selected_pages
    }

    def _base_row() -> dict[str, int]:
        return {column: 0 for column in page_columns}

    def _max_row() -> dict[str, int]:
        return dict(max_values)

    rows: list[dict[str, int | str]] = []
    rows.append(
        {
            "row_kind": "base_pages",
            "row_label": "base_pages",
            "target_page_index": -1,
            "target_column": "",
            "target_update_count": 0,
            "target_break_after_page_accesses": -1,
            **_base_row(),
        }
    )
    rows.append(
        {
            "row_kind": "no_break",
            "row_label": "no_break",
            "target_page_index": -1,
            "target_column": "",
            "target_update_count": 0,
            "target_break_after_page_accesses": -1,
            **_max_row(),
        }
    )

    for rank, page in enumerate(hottest, start=1):
        target_column = breakpoint_column_name(page.page_index)
        row = _max_row()
        row[target_column] = 0
        rows.append(
            {
                "row_kind": "split_only",
                "row_label": f"hot page #{rank}",
                "target_page_index": page.page_index,
                "target_column": target_column,
                "target_update_count": page.update_count,
                "target_break_after_page_accesses": 1,
                **row,
            }
        )

    for rank, page in enumerate(random_pages, start=1):
        target_column = breakpoint_column_name(page.page_index)
        row = _max_row()
        row[target_column] = 0
        rows.append(
            {
                "row_kind": "split_only",
                "row_label": f"random page #{rank}",
                "target_page_index": page.page_index,
                "target_column": target_column,
                "target_update_count": page.update_count,
                "target_break_after_page_accesses": 1,
                **row,
            }
        )

    return rows


def write_breakpoints(rows: list[dict[str, int | str]], output_path: Path) -> None:
    """Write the generated matrix to one Parquet file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(output_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read a deterministic GUPS page-summary CSV and emit a breakpoint "
            "matrix with base_pages, no_break, hottest-page, and random-page rows."
        )
    )
    parser.add_argument("page_summary", type=Path, help="Path to GUPS page summary CSV.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/gups_breakpoints.parquet"),
        help="Output Parquet file (default: data/gups_breakpoints.parquet).",
    )
    parser.add_argument(
        "--hot-pages",
        type=int,
        default=10,
        help="Number of hottest split-only page rows to emit (default: 10).",
    )
    parser.add_argument(
        "--random-rows",
        type=int,
        default=3,
        help="Number of random split-only page rows to emit (default: 3).",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=0,
        help="Stable RNG seed for random-row selection (default: 0).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    summaries = load_page_summaries(args.page_summary)
    rows = generate_breakpoint_rows(
        summaries,
        hot_pages=args.hot_pages,
        random_rows=args.random_rows,
        random_seed=args.random_seed,
    )
    write_breakpoints(rows, args.output)


if __name__ == "__main__":
    main()

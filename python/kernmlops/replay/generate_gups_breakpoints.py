"""Generate deterministic GUPS breakpoint matrices from page-summary CSVs.

The input CSV comes from the seeded GUPS calibration pass and contains one row
per 2 MiB table region per repeat. This script validates that the page summary
is stable across repeats, then emits one of these deterministic matrix shapes:

1. ``base_pages`` sentinel row
2. ``no_break`` intact-THP baseline
3. single-page hot/random rows, cumulative rows, or explicit chunked rank
   ranges
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


def _chunk_label(*, page_group: str, start_rank: int, end_rank: int) -> str:
    """Return the exact row label for one chunked rank range."""
    if page_group == "hot":
        return f"hot pages {start_rank} to {end_rank}"
    if page_group == "least_hot":
        return f"least hot pages {start_rank} to {end_rank}"
    raise RuntimeError(f"Unsupported GUPS chunk page_group: {page_group}")


def _slice_rank_range(
    page_summaries: list[PageSummary],
    *,
    start_rank: int,
    end_rank: int,
    page_group: str,
) -> list[PageSummary]:
    """Return one inclusive 1-indexed rank slice or fail fast."""
    if end_rank > len(page_summaries):
        raise RuntimeError(
            f"Need ranks {start_rank} to {end_rank} for {page_group}, "
            f"but only {len(page_summaries)} pages are available."
        )
    return page_summaries[start_rank - 1 : end_rank]


def _unique_pages(page_groups: list[list[PageSummary]]) -> list[PageSummary]:
    """Return pages from several ordered groups without duplicate page columns."""
    selected: list[PageSummary] = []
    seen_indices: set[int] = set()
    for page_group in page_groups:
        for page in page_group:
            if page.page_index in seen_indices:
                continue
            selected.append(page)
            seen_indices.add(page.page_index)
    return selected


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
    multi_page_counts: tuple[int, int] | None = None,
    multi_page_count_list: tuple[int, ...] = (),
    multi_page_groups: tuple[str, ...] = (),
    hot_page_chunks: tuple[tuple[int, int], ...] = (),
    least_hot_page_chunks: tuple[tuple[int, int], ...] = (),
) -> list[dict[str, int | str]]:
    """Build a Redis-style breakpoint matrix for deterministic GUPS.

    Args:
        page_summaries: Stable per-page calibration summaries.
        hot_pages: Number of hottest split-only rows to emit.
        random_rows: Number of random split-only rows to emit from the
            remaining pages.
        random_seed: Stable RNG seed for random-page row selection.
        multi_page_counts: Optional inclusive ``(start, end)`` count range for
            cumulative multi-page rows. When set, single-page rows are replaced
            by cumulative rows for the requested groups.
        multi_page_count_list: Optional explicit count list for sparse
            cumulative rows such as ``(10, 100, 1000)``.
        multi_page_groups: Page groups to emit for cumulative rows.
        hot_page_chunks: Explicit hot rank ranges such as ``((1, 10), (11, 20))``.
        least_hot_page_chunks: Explicit least-hot tail rank ranges such as
            ``((1, 10),)``.

    Returns:
        Ordered row dictionaries ready to write to Parquet.
    """
    if multi_page_counts is not None and (hot_page_chunks or least_hot_page_chunks):
        raise RuntimeError(
            "Cannot combine --multi-page-counts with --hot-page-chunks or "
            "--least-hot-page-chunks."
        )
    if multi_page_count_list and (hot_page_chunks or least_hot_page_chunks):
        raise RuntimeError(
            "Cannot combine --multi-page-count-list with --hot-page-chunks or "
            "--least-hot-page-chunks."
        )
    if multi_page_counts is not None and multi_page_count_list:
        raise RuntimeError(
            "Cannot combine --multi-page-counts with --multi-page-count-list."
        )
    multi_page_mode = multi_page_counts is not None or bool(multi_page_count_list)
    if multi_page_mode:
        if multi_page_counts is not None:
            start_count, end_count = multi_page_counts
            count_values = tuple(range(start_count, end_count + 1))
        else:
            count_values = multi_page_count_list
            start_count = count_values[0]
            end_count = max(count_values)
        if start_count <= 0 or end_count < start_count or any(
            count <= 0 for count in count_values
        ):
            raise RuntimeError(
                "Cumulative GUPS counts must be positive."
            )
        if len(set(count_values)) != len(count_values):
            raise RuntimeError("Cumulative GUPS count values must be unique.")
        unknown_groups = set(multi_page_groups) - {"hot", "random"}
        if unknown_groups:
            unknown_text = ", ".join(sorted(unknown_groups))
            raise RuntimeError(f"Unknown --multi-page-groups values: {unknown_text}")
        if not multi_page_groups:
            raise RuntimeError(
                "Cumulative GUPS rows require at least one --multi-page-groups value."
            )
    else:
        count_values = ()
        end_count = 0

    if not page_summaries:
        return [
            {
                "row_kind": "base_pages",
                "row_label": "base_pages",
                "target_page_index": -1,
                "target_column": "",
                "target_update_count": 0,
                "target_break_after_page_accesses": -1,
                "target_page_indices": "",
                "target_page_count": 0,
                "page_group": "",
            }
        ]

    chunk_mode = bool(hot_page_chunks or least_hot_page_chunks)
    max_multi_count = end_count if multi_page_mode else 0
    max_hot_chunk_rank = max((end for _, end in hot_page_chunks), default=0)
    max_least_hot_chunk_rank = max(
        (end for _, end in least_hot_page_chunks),
        default=0,
    )
    hottest_count = max(
        hot_pages if not chunk_mode else 0,
        max_multi_count if "hot" in multi_page_groups else 0,
        max_hot_chunk_rank,
    )
    hottest = page_summaries[:hottest_count]
    remaining = page_summaries[hottest_count:]
    rng = random.Random(random_seed)
    if chunk_mode:
        random_count = 0
    elif multi_page_mode and "random" in multi_page_groups:
        random_count = max_multi_count
    elif multi_page_mode:
        random_count = 0
    else:
        random_count = min(random_rows, len(remaining))
    if multi_page_mode and "random" in multi_page_groups and random_count > len(remaining):
        raise RuntimeError(
            f"Need {random_count} random GUPS pages after the hottest {hottest_count}, "
            f"but only {len(remaining)} are available."
        )
    random_pages = rng.sample(remaining, k=random_count)
    least_hot_pages = (
        page_summaries[-max_least_hot_chunk_rank:]
        if max_least_hot_chunk_rank > 0
        else []
    )
    selected_pages = _unique_pages([hottest, random_pages, least_hot_pages])
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
            "target_page_indices": "",
            "target_page_count": 0,
            "page_group": "",
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
            "target_page_indices": "",
            "target_page_count": 0,
            "page_group": "",
            **_max_row(),
        }
    )

    if chunk_mode:
        chunk_specs: list[tuple[str, str, list[PageSummary]]] = []
        least_hot_ranked = list(reversed(page_summaries))

        for start_rank, end_rank in hot_page_chunks:
            selected = _slice_rank_range(
                page_summaries,
                start_rank=start_rank,
                end_rank=end_rank,
                page_group="hot pages",
            )
            chunk_specs.append(
                (
                    "hot",
                    _chunk_label(
                        page_group="hot",
                        start_rank=start_rank,
                        end_rank=end_rank,
                    ),
                    selected,
                )
            )

        for start_rank, end_rank in least_hot_page_chunks:
            selected = _slice_rank_range(
                least_hot_ranked,
                start_rank=start_rank,
                end_rank=end_rank,
                page_group="least hot pages",
            )
            chunk_specs.append(
                (
                    "least_hot",
                    _chunk_label(
                        page_group="least_hot",
                        start_rank=start_rank,
                        end_rank=end_rank,
                    ),
                    selected,
                )
            )

        for page_group, row_label, selected in chunk_specs:
            row = _max_row()
            target_columns = [
                breakpoint_column_name(page.page_index) for page in selected
            ]
            target_indices = [page.page_index for page in selected]
            for target_column in target_columns:
                row[target_column] = 0
            rows.append(
                {
                    "row_kind": "split_chunk",
                    "row_label": row_label,
                    "target_page_index": -1,
                    "target_column": ",".join(target_columns),
                    "target_update_count": sum(
                        page.update_count for page in selected
                    ),
                    "target_break_after_page_accesses": 1,
                    "target_page_indices": ",".join(
                        str(index) for index in target_indices
                    ),
                    "target_page_count": len(selected),
                    "page_group": page_group,
                    **row,
                }
            )

        return rows

    if multi_page_mode:
        def _multi_row(
            *,
            group: str,
            count: int,
            pages: list[PageSummary],
        ) -> dict[str, int | str]:
            selected = pages[:count]
            row = _max_row()
            target_columns = [breakpoint_column_name(page.page_index) for page in selected]
            target_indices = [page.page_index for page in selected]
            for column in target_columns:
                row[column] = 0
            return {
                "row_kind": "split_multi",
                "row_label": f"{group} {count} pages",
                "target_page_index": -1,
                "target_column": ",".join(target_columns),
                "target_update_count": sum(page.update_count for page in selected),
                "target_break_after_page_accesses": 1,
                "target_page_indices": ",".join(str(index) for index in target_indices),
                "target_page_count": count,
                "page_group": group,
                **row,
            }

        if "hot" in multi_page_groups and len(hottest) < end_count:
            raise RuntimeError(
                f"Need {end_count} hot GUPS pages, but only {len(hottest)} are available."
            )
        if "random" in multi_page_groups and len(random_pages) < end_count:
            raise RuntimeError(
                f"Need {end_count} random GUPS pages, but only {len(random_pages)} are available."
            )

        for group in multi_page_groups:
            source_pages = hottest if group == "hot" else random_pages
            for count in count_values:
                rows.append(_multi_row(group=group, count=count, pages=source_pages))

        return rows

    hottest = hottest[:hot_pages]
    random_pages = random_pages[:random_rows]

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
                "target_page_indices": str(page.page_index),
                "target_page_count": 1,
                "page_group": "hot",
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
                "target_page_indices": str(page.page_index),
                "target_page_count": 1,
                "page_group": "random",
                **row,
            }
        )

    return rows


def write_breakpoints(rows: list[dict[str, int | str]], output_path: Path) -> None:
    """Write the generated matrix to one Parquet file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(output_path)


def parse_multi_page_counts(raw_counts: str) -> tuple[int, int]:
    """Parse an inclusive multi-page count range like ``2:10``."""
    pieces = raw_counts.split(":")
    if len(pieces) != 2:
        raise argparse.ArgumentTypeError("--multi-page-counts must look like START:END.")
    try:
        start_count = int(pieces[0])
        end_count = int(pieces[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--multi-page-counts must contain integer START and END values."
        ) from exc
    if start_count <= 0 or end_count < start_count:
        raise argparse.ArgumentTypeError(
            "--multi-page-counts must satisfy 0 < START <= END."
        )
    return start_count, end_count


def parse_multi_page_count_list(raw_counts: str) -> tuple[int, ...]:
    """Parse explicit cumulative count values like ``10,100,1000``."""
    counts: list[int] = []
    for raw_count in raw_counts.split(","):
        count_text = raw_count.strip()
        if not count_text:
            continue
        try:
            count = int(count_text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "--multi-page-count-list must contain integer count values."
            ) from exc
        if count <= 0:
            raise argparse.ArgumentTypeError(
                "--multi-page-count-list values must be positive."
            )
        counts.append(count)

    if not counts:
        raise argparse.ArgumentTypeError("--multi-page-count-list cannot be empty.")
    if len(set(counts)) != len(counts):
        raise argparse.ArgumentTypeError(
            "--multi-page-count-list values must be unique."
        )
    return tuple(counts)


def parse_multi_page_groups(raw_groups: str) -> tuple[str, ...]:
    """Parse a comma-separated multi-page group list."""
    groups = tuple(group.strip() for group in raw_groups.split(",") if group.strip())
    unknown_groups = set(groups) - {"hot", "random"}
    if unknown_groups:
        unknown_text = ", ".join(sorted(unknown_groups))
        raise argparse.ArgumentTypeError(
            f"--multi-page-groups only accepts hot and random, got: {unknown_text}"
        )
    if not groups:
        raise argparse.ArgumentTypeError("--multi-page-groups cannot be empty.")
    return groups


def parse_page_chunks(raw_chunks: str) -> tuple[tuple[int, int], ...]:
    """Parse a comma-separated list of inclusive page-rank ranges.

    Example output:
        ((1, 10), (11, 20))

    Args:
        raw_chunks: CLI text such as ``1:10,11:20``.

    Returns:
        Ordered inclusive ``(start_rank, end_rank)`` tuples.

    Raises:
        argparse.ArgumentTypeError: If one range is malformed.
    """
    chunks: list[tuple[int, int]] = []
    for raw_chunk in raw_chunks.split(","):
        chunk_text = raw_chunk.strip()
        if not chunk_text:
            continue
        pieces = chunk_text.split(":")
        if len(pieces) != 2:
            raise argparse.ArgumentTypeError(
                "Chunk ranges must look like START:END."
            )
        try:
            start_rank = int(pieces[0])
            end_rank = int(pieces[1])
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "Chunk ranges must contain integer START and END values."
            ) from exc
        if start_rank <= 0 or end_rank < start_rank:
            raise argparse.ArgumentTypeError(
                "Chunk ranges must satisfy 0 < START <= END."
            )
        chunks.append((start_rank, end_rank))

    if not chunks:
        raise argparse.ArgumentTypeError("Chunk range list cannot be empty.")
    return tuple(chunks)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read a deterministic GUPS page-summary CSV and emit a breakpoint "
            "matrix with base_pages, no_break, single-page rows, cumulative rows, "
            "or explicit chunked rank-range rows."
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
    parser.add_argument(
        "--multi-page-counts",
        type=parse_multi_page_counts,
        default=None,
        help="Optional inclusive count range for cumulative rows, e.g. 2:10.",
    )
    parser.add_argument(
        "--multi-page-count-list",
        type=parse_multi_page_count_list,
        default=(),
        help="Optional explicit cumulative counts, e.g. 10,100,1000.",
    )
    parser.add_argument(
        "--multi-page-groups",
        type=parse_multi_page_groups,
        default=("hot", "random"),
        help="Comma-separated cumulative groups when --multi-page-counts is set (default: hot,random).",
    )
    parser.add_argument(
        "--hot-page-chunks",
        type=parse_page_chunks,
        default=(),
        help="Optional comma-separated hot rank ranges, e.g. 1:10,11:20.",
    )
    parser.add_argument(
        "--least-hot-page-chunks",
        type=parse_page_chunks,
        default=(),
        help="Optional comma-separated least-hot rank ranges, e.g. 1:10.",
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
        multi_page_counts=args.multi_page_counts,
        multi_page_count_list=args.multi_page_count_list,
        multi_page_groups=args.multi_page_groups,
        hot_page_chunks=args.hot_page_chunks,
        least_hot_page_chunks=args.least_hot_page_chunks,
    )
    write_breakpoints(rows, args.output)


if __name__ == "__main__":
    main()

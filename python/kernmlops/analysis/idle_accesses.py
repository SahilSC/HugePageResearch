#!/usr/bin/env python3
"""
Plot per-page access count distribution from track_redis_accesses.py output.

Two plot types are available (--type):
  hist  Histogram of page access counts
  line  Rank-frequency line: pages sorted by access count descending.
        A straight line on log–log axes confirms a Zipfian distribution.

Usage:
    python3 scripts/plot_redis_accesses.py [--input accesses.csv]
                                           [--output accesses_hist.png]
                                           [--type {hist,line}]
                                           [--bins N] [--no-logy]
"""

import argparse
import csv
import gzip
import sys
from pathlib import Path

import matplotlib
import numpy as np  # rank array for line plot
import pyarrow.parquet as pq

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# ── Paper-quality style ───────────────────────────────────────────────────────
plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "DejaVu Serif", "serif"],
        "font.size": 9,
        "axes.labelsize": 9,
        "axes.titlesize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "axes.linewidth": 0.8,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.minor.width": 0.5,
        "ytick.minor.width": 0.5,
        "xtick.major.size": 3.5,
        "ytick.major.size": 3.5,
        "xtick.minor.size": 2.0,
        "ytick.minor.size": 2.0,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.05,
    }
)

BAR_COLOR = "#2166ac"  # ColorBrewer blue, colorblind-safe
DEFAULT_BINS = 50


# ── Data loading ──────────────────────────────────────────────────────────────


def load_parquet(path: str) -> tuple[list[dict], int, float]:
    table = pq.read_table(
        path, columns=["pfn", "accesses", "is_thp", "is_huge", "n_polls", "elapsed_s"]
    )
    col = table.column
    n_polls = int(col("n_polls")[0].as_py())
    elapsed = float(col("elapsed_s")[0].as_py())
    rows = [
        {
            "pfn": pfn,
            "accesses": acc,
            "is_thp": thp,
            "is_huge": huge,
        }
        for pfn, acc, thp, huge in zip(
            col("pfn").to_pylist(),
            col("accesses").to_pylist(),
            col("is_thp").to_pylist(),
            col("is_huge").to_pylist(),
        )
    ]
    return rows, n_polls, elapsed


def load_csv(path: str) -> tuple[list[dict], int, float]:
    rows: list[dict] = []
    n_polls = 0
    elapsed = 0.0
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", newline="") as f:
        for row in csv.DictReader(f):
            n_polls = int(row["n_polls"])
            elapsed = float(row["elapsed_s"])
            rows.append(
                {
                    "pfn": int(row["pfn"]),
                    "accesses": int(row["accesses"]),
                    "is_thp": int(row["is_thp"]),
                    "is_huge": int(row["is_huge"]),
                }
            )
    return rows, n_polls, elapsed


def load_data(path: str) -> tuple[list[dict], int, float]:
    if path.endswith(".parquet"):
        return load_parquet(path)
    return load_csv(path)


# ── Plot functions ────────────────────────────────────────────────────────────


def _metadata_text(n_pages: int, poll_rate: float, elapsed: float) -> str:
    return f"{n_pages:,} pages · {poll_rate:.2g} polls/s · {elapsed:.0f} s"


def plot_hist(
    rows: list[dict],
    n_polls: int,
    elapsed: float,
    out_path: str,
    bins: int = DEFAULT_BINS,
    log: bool = False,
) -> None:
    nonzero = [r["accesses"] for r in rows if r["accesses"] > 0]
    if not nonzero:
        sys.exit("error: no pages with non-zero access counts")

    poll_rate = n_polls / elapsed if elapsed > 0 else 0.0

    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    ax.hist(nonzero, bins=bins, color=BAR_COLOR, edgecolor="white", linewidth=0.3)
    ax.set_xlabel(f"Access count (out of {n_polls} polls)")
    ax.set_ylabel("Number of pages")

    if log:
        ax.set_xscale("log")
        ax.xaxis.set_minor_locator(ticker.LogLocator(base=10, subs="all"))
        ax.xaxis.set_minor_formatter(ticker.NullFormatter())
        ax.set_yscale("log")
        ax.yaxis.set_minor_locator(ticker.LogLocator(base=10, subs="all"))
        ax.yaxis.set_minor_formatter(ticker.NullFormatter())
    else:
        ax.xaxis.set_minor_locator(ticker.AutoMinorLocator(2))

    ax.text(
        0.98,
        0.97,
        _metadata_text(len(rows), poll_rate, elapsed),
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=6.5,
        color="#555555",
    )

    out = Path("figures") / out_path
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out)
    print(f"[INFO] plot saved to {out}")


def plot_line(
    rows: list[dict],
    n_polls: int,
    elapsed: float,
    out_path: str,
    log: bool = False,
) -> None:
    nonzero = sorted(
        (r["accesses"] for r in rows if r["accesses"] > 0 and r["accesses"] != n_polls),
        reverse=True,
    )
    if not nonzero:
        sys.exit("error: no pages with non-zero access counts")

    poll_rate = n_polls / elapsed if elapsed > 0 else 0.0
    ranks = np.arange(1, len(nonzero) + 1)

    fig, ax = plt.subplots(figsize=(3.5, 2.6), constrained_layout=True)
    ax.plot(ranks, nonzero, color=BAR_COLOR, linewidth=0.9)
    ax.set_xlabel("Page rank")
    ax.set_ylabel(f"Access count (out of {n_polls})")

    if log:
        ax.set_xscale("log")
        ax.xaxis.set_major_locator(ticker.LogLocator(base=10))
        ax.xaxis.set_minor_locator(ticker.LogLocator(base=10, subs="all"))
        ax.xaxis.set_minor_formatter(ticker.NullFormatter())
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(ticker.LogLocator(base=10))
        ax.yaxis.set_minor_locator(ticker.LogLocator(base=10, subs="all"))
        ax.yaxis.set_minor_formatter(ticker.NullFormatter())
    else:
        ax.xaxis.set_major_locator(ticker.AutoLocator())
        ax.xaxis.set_minor_locator(ticker.AutoMinorLocator(2))
        ax.yaxis.set_major_locator(ticker.AutoLocator())
        ax.yaxis.set_minor_locator(ticker.AutoMinorLocator(2))

    ax.text(
        0.98,
        0.97,
        _metadata_text(len(rows), poll_rate, elapsed),
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=6.5,
        color="#555555",
    )

    out = Path("figures") / out_path
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out)
    print(f"[INFO] plot saved to {out}")


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--input",
        default="accesses.parquet",
        metavar="FILE",
        help="Input file: .parquet or .csv/.csv.gz (default: accesses.parquet)",
    )
    p.add_argument(
        "--output",
        default="accesses.png",
        metavar="FILE",
        help="Output path; use .pdf for vector output (default: accesses.png)",
    )
    p.add_argument(
        "--type",
        dest="plot_type",
        choices=["hist", "line"],
        default="line",
        help="Plot type: histogram (default) or rank-frequency line",
    )
    p.add_argument(
        "--bins",
        type=int,
        default=DEFAULT_BINS,
        help=f"Histogram bins (default: {DEFAULT_BINS}; hist mode only)",
    )
    p.add_argument(
        "--log", action="store_true", help="Log axes instead of linear scale"
    )
    args = p.parse_args()

    if not Path(args.input).exists():
        sys.exit(f"error: {args.input} not found — run track_idle_accesses.py first")

    rows, n_polls, elapsed = load_data(args.input)

    if args.plot_type == "line":
        plot_line(rows, n_polls, elapsed, args.output, log=args.log)
    else:
        plot_hist(rows, n_polls, elapsed, args.output, bins=args.bins, log=args.log)


if __name__ == "__main__":
    main()

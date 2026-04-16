import os
import re
import sys
from typing import Dict, List

import numpy
import pandas as pd
import seaborn as sns
from matplotlib import pyplot as plt

"""
Script to plot redis-benchmark runtimes from benchmark-redis-log
"""

pattern = re.compile(r"^\[OVERALL\], RunTime\(ms\), (\d+)$", re.MULTILINE)


def load_runtimes(file: str) -> List[float]:
    with open(file) as f:
        logs = f.read()
        runtimes = [float(m) / 1000 for m in pattern.findall(logs)]
    return runtimes


def plot_runtimes(
    total_times: Dict[str, List[float]],
    load_times: Dict[str, List[float]],
    run_times: Dict[str, List[float]],
    log_dir: str,
) -> None:
    rows = []
    for config in total_times:
        for rt in total_times[config]:
            rows.append({"config": config, "metric": "total", "runtime_s": rt})
        for rt in load_times[config]:
            rows.append({"config": config, "metric": "load", "runtime_s": rt})
        for rt in run_times[config]:
            rows.append({"config": config, "metric": "run", "runtime_s": rt})
    df = pd.DataFrame(rows)

    configs = sorted(df["config"].unique().tolist())
    metrics = ["total", "load", "run"]

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(4 * len(configs), 5))
    sns.boxplot(
        data=df,
        x="config",
        y="runtime_s",
        hue="metric",
        hue_order=metrics,
        order=configs,
        showmeans=True,
        meanprops={
            "marker": "^",
            "markerfacecolor": "green",
            "markeredgecolor": "green",
            "markersize": 8,
        },
        ax=ax,
    )
    ax.set_xlabel("THP Config")
    ax.set_ylabel("Time (s)")
    ax.set_title("Redis Benchmark Runtimes by THP Config")
    ax.legend(title="Metric")
    fig.tight_layout()
    fig.savefig("boxplot.png", dpi=150)
    print("[INFO] plotted boxplot.png")
    plt.show()


def main() -> None:
    if len(sys.argv) != 2:
        raise Exception("Usage: thp_runtimes.py <log_dir>")
    log_dir = sys.argv[1]

    total_times: Dict[str, List[float]] = {}
    load_times: Dict[str, List[float]] = {}
    run_times: Dict[str, List[float]] = {}
    for file in filter(lambda f: f.endswith(".log"), os.listdir(log_dir)):
        runtime = load_runtimes(os.path.join(log_dir, file))
        config = file[: file.find(".")]
        load = [runtime[i] for i in range(0, len(runtime), 2)]
        run = [runtime[i] for i in range(1, len(runtime), 2)]
        total = [l + r for l, r in zip(load, run)]
        load_times[config] = load
        run_times[config] = run
        total_times[config] = total
        print(config, runtime)

    for c, r in total_times.items():
        n = len(r)
        mean, median, std = numpy.mean(r), numpy.median(r), numpy.std(r)
        q1, q3 = numpy.percentile(r, 25), numpy.percentile(r, 75)
        iqr = q3 - q1
        print(c, n, mean, median, std, std / mean, iqr)

    plot_runtimes(total_times, load_times, run_times, log_dir)


if __name__ == "__main__":
    main()

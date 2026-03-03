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


def plot_runtimes(runtimes: Dict[str, List[float]], log_dir: str) -> None:
    df = pd.DataFrame(
        [
            {"config": config, "runtime_s": rt}
            for config, values in runtimes.items()
            for rt in values
        ]
    )

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(7, 5))
    configs = df["config"].unique().tolist()
    sns.boxplot(
        data=df,
        x="config",
        y="runtime_s",
        order=configs,
        showmeans=True,
        meanprops={
            "marker": "^",
            "markerfacecolor": "green",
            "markeredgecolor": "green",
            "markersize": 8,
        },
        # flierprops={"marker": "o", "markerfacecolor": "red", "markersize": 5},
        ax=ax,
    )
    means = df.groupby("config")["runtime_s"].mean()
    for i, config in enumerate(configs):
        mean_val = means[config]
        ax.annotate(
            f"{mean_val:.2f}s",
            xy=(i, mean_val),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color="black",
        )
    ax.set_xlabel("THP Config")
    ax.set_ylabel("RunTime (s)")
    ax.set_title("Redis Benchmark Runtimes by THP Config")
    fig.tight_layout()
    fig.savefig("boxplot.png", dpi=150)
    plt.show()


def main() -> None:
    if len(sys.argv) != 2:
        raise Exception("Usage: thp_runtimes.py <log_dir>")
    log_dir = sys.argv[1]

    runtimes = {}
    for file in os.listdir(log_dir):
        runtime = load_runtimes(os.path.join(log_dir, file))
        runtime.pop(0)  # Drop load time
        config = file[: file.find(".")]
        runtimes[config] = runtime

    for c, r in runtimes.items():
        mean, median, std = numpy.mean(r), numpy.median(r), numpy.std(r)
        q1, q3 = numpy.percentile(r, 25), numpy.percentile(r, 75)
        iqr = q3 - q1
        print(c, mean, median, std, std / mean, iqr)

    plot_runtimes(runtimes, log_dir)


if __name__ == "__main__":
    main()

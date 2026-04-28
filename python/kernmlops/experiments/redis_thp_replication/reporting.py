"""Shared HTML and PNG reporting helpers for the overnight one-off experiments."""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl

from experiments.redis_thp_replication.common import _chown_path, write_markdown


def _embed_png(path: Path) -> str:
    """Return one PNG file as a base64 data URL."""

    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def grouped_bar_png(
    *,
    categories: list[str],
    series: list[dict[str, Any]],
    title: str,
    ylabel: str,
    output_path: Path,
) -> None:
    """Render one grouped bar chart with stddev error bars."""

    fig, ax = plt.subplots(figsize=(11, 6))
    x = np.arange(len(categories))
    width = 0.8 / max(len(series), 1)

    for index, item in enumerate(series):
        offsets = x - 0.4 + width / 2 + index * width
        ax.bar(
            offsets,
            item["means"],
            width=width,
            yerr=item["stds"],
            color=item["color"],
            label=item["label"],
            capsize=4,
            edgecolor="black",
            linewidth=0.6,
        )

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.grid(axis="y", linestyle="--", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)
    _chown_path(output_path)


def table_to_html(df: pl.DataFrame) -> str:
    """Convert a small dataframe into a simple HTML table."""

    if df.is_empty():
        return "<p>No rows.</p>"

    header = "".join(f"<th>{column}</th>" for column in df.columns)
    rows = []
    for row in df.iter_rows():
        cells = []
        for value in row:
            if isinstance(value, float):
                if math.isfinite(value):
                    cells.append(f"<td>{value:.3f}</td>")
                else:
                    cells.append("<td>n/a</td>")
            else:
                cells.append(f"<td>{value}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        "<table><thead><tr>"
        + header
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def write_html_report(
    *,
    title: str,
    subtitle: str,
    sections: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Write a standalone HTML page with embedded PNGs and tables."""

    body = []
    for section in sections:
        image_html = ""
        if section.get("image_path") is not None:
            image_html = (
                f'<img src="{_embed_png(section["image_path"])}" '
                'style="max-width:100%; border:1px solid #d0d7de; border-radius:12px;" />'
            )
        table_html = ""
        if section.get("table") is not None:
            table_html = table_to_html(section["table"])
        bullets_html = ""
        if section.get("bullets"):
            bullets_html = "<ul>" + "".join(
                f"<li>{bullet}</li>" for bullet in section["bullets"]
            ) + "</ul>"
        body.append(
            "\n".join(
                [
                    f"<section><h2>{section['title']}</h2>",
                    f"<p>{section['description']}</p>",
                    image_html,
                    bullets_html,
                    table_html,
                    "</section>",
                ]
            )
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <style>
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      background: #f6f8fb;
      color: #1f2328;
    }}
    main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
    }}
    section {{
      background: white;
      padding: 20px;
      border-radius: 16px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
      margin-bottom: 20px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 16px;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #d8dee4;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #f6f8fb;
    }}
    code {{
      background: #eef2f6;
      padding: 2px 6px;
      border-radius: 6px;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{title}</h1>
      <p>{subtitle}</p>
    </header>
    {''.join(body)}
  </main>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")
    _chown_path(output_path)


def write_markdown_table_report(
    *,
    title: str,
    paragraphs: list[str],
    tables: list[tuple[str, pl.DataFrame]],
    output_path: Path,
) -> None:
    """Write one simple markdown companion report."""

    parts = [f"# {title}", ""]
    for paragraph in paragraphs:
        parts.extend([paragraph, ""])
    for heading, df in tables:
        parts.extend([f"## {heading}", "", df.write_csv(separator="|"), ""])
    write_markdown(output_path, "\n".join(parts))

# Get Graph for Perf

## Instructions (Perf Graph PNG)

Use the graph command with `-m` (matplotlib). Without `-m`, the tool saves `.plt` text plots instead of `.png`.

```bash
.venv/bin/python python/kernmlops collect graph \
  -d data/curated \
  -o data/graphs \
  -c 3fa83393-3d77-4415-a4ec-0e912e6ab720 \
  -m
```

Replace `-c` with your collection id (or a unique prefix).

## Where the PNG goes

Graphs are written under:

```text
data/graphs/<benchmark>/<collection_id>/
```

Example for the Redis run above:

```text
data/graphs/redis/3fa83393-3d77-4415-a4ec-0e912e6ab720/
```

## Notes

- This command graphs all available tables for that collection, including perf tables.
- If you get `ModuleNotFoundError: bcc`, run inside the project/container environment where `bcc` is installed (or install the Python `bcc` package for this venv).
- Current perf graph filenames are based on the component name (for example `cpu_performance.png`, `system_performance.png`), so multiple perf graphs with the same component may overwrite each other.

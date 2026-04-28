# Replay Package Migration

## What Changed

This change moved the Redis replay workflow into one package:

- `python/kernmlops/replay/replay_trace.py`
- `python/kernmlops/replay/generate_breakpoints.py`
- `python/kernmlops/replay/hardware_collectors.py`
- `python/kernmlops/replay/capture_redis_trace.sh`

The old mixed layout is gone. The legacy flat replay file was not kept as a
shim because this cleanup was explicitly not about backwards compatibility.

## Why This Works

The repo already treats `python/kernmlops` as the Python import root, so a new
top-level `replay` package fits the existing import model cleanly.

The replay script kept the same runtime behavior and CLI flags. The main code
change was updating its direct-file bootstrap so this still works:

```bash
python python/kernmlops/replay/replay_trace.py ...
```

The moved capture script now resolves the repo root from its new location
before writing `data/redis_traces/...`, so relocating the script does not send
artifacts into the wrong folder.

## Other Approaches Considered

Keeping replay under `data_collection/replay` would have been a smaller path
change, but it would still leave replay visually grouped under collector code
instead of standing on its own.

Putting replay under `python/replay` would also separate it, but it would not
match the rest of this repo's import shape. We would have introduced a special
case just for replay.

## Pros

- Replay capture, generation, execution, and replay-only counters now live in
  one place.
- Import paths are shorter and easier to read: `replay.*`.
- The runbook now points at one consistent folder instead of mixing
  `analysis/`, `data_collection/`, and `scripts/`.

## Cons

- Any local notes or old commands that still point at the removed paths need to
  be updated before reuse.
- Historical reports still mention the older layout, so readers need to treat
  them as historical context rather than current commands.

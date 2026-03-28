# Redis VAPTR Module

This directory contains the small Redis module used by the VAPTR collector
hook. It adds two commands:

- `VAPTR [FIELD <field>] <key>...`
  returns each key name plus the address of the underlying Redis value pointer,
  or `(nil)` if the module cannot expose a direct pointer for that key.
- `VAPTR.DEBUG <key> [field] [preview_len]`
  returns extra type, encoding, and value-preview information that is useful
  while debugging the module.

## What It Supports

The current implementation handles two direct-pointer cases:

- Redis strings via `RedisModule_StringDMA`
- Redis hashes stored in hash-table encoding, using a pinned subset of the
  Redis 7.4.2 internal object layout from
  [`redis_internals_7_4.h`](./redis_internals_7_4.h)

Hashes stored in compact/listpack encodings do not expose a direct stable value
pointer here, so the module returns `(nil)` for those cases.

## Build

From the repository root:

- `make -C redis-module`

The `Makefile` downloads `redismodule.h` from the Redis `7.4.2` source tree if
it is not already present locally, then builds `vaptr.so`.

## How The Collector Uses It

When the collector enables the `vaptr` hook for the Redis benchmark, the
benchmark build path auto-builds `redis-module/vaptr.so` before starting Redis.
If the shared object exists, the benchmark launches Redis with:

- `--loadmodule /abs/path/to/redis-module/vaptr.so`

That lets [`vaptr_hook.py`](../python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py)
query Redis for object addresses and then join those addresses with the
page-access helper.

# VAPTR Notes

## Short answer

Yes: under the supported Redis encodings, the captured pointer can be used to return the actual value bytes associated with the sampled key.

No: it does **not** point to the Redis key name string itself.

For the YCSB Redis workload in this repo, the relevant object is the hash field value, usually `field0`, stored under a Redis key like `user6284781860667377211`.

## What was wrong before

There were three separate problems:

1. The Redis module only handled top-level string keys.
   YCSB stores Redis records as hashes, so `vaptr` returned nothing useful for the benchmark.

2. The Python hook guessed fake key names.
   It sampled keys like `user0000000000000000`, but YCSB defaults to hashed `user...` keys, so the hook often queried keys that did not exist.

3. The collector could run without a built Redis module.
   If `redis-module/vaptr.so` was missing, the benchmark could start without the `VAPTR` command being available.

## Changes made

### 1. Redis module

File: `redis-module/vaptr.c`

- `VAPTR` now supports:
  - top-level Redis string keys via `RedisModule_StringDMA`
  - YCSB-style Redis hash keys by looking up a field value such as `field0`
- For hash keys, the module only reports a direct pointer when the hash encoding is `hashtable`
- If the hash is still `listpack`, the module returns unresolved instead of returning a misleading copied pointer
- Added `VAPTR.DEBUG`

`VAPTR.DEBUG` returns:

1. key
2. redis type
3. redis encoding
4. whether the pointer is `direct` or `unresolved`
5. field name
6. pointer address
7. value length
8. byte preview read from that pointer

### 2. Python hook

File: `python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py`

- The hook now calls:

```bash
redis-cli --raw VAPTR FIELD field0 ...
```

- The hook no longer invents keys
- It discovers real benchmark keys with Redis `SCAN MATCH user*`
- This makes the hook work with YCSB's actual key format

### 3. Collection path

Files:

- `python/kernmlops/cli/collect.py`
- `python/kernmlops/data_collection/__init__.py`
- `python/kernmlops/kernmlops_benchmark/redis.py`

Changes:

- If the `vaptr` hook is active, collection ensures the Redis module is built
- Redis config can now pass the sampled field name (`vaptr_field_name`)

### 4. End-to-end test config

File: `config/redis_vaptr_e2e.yaml`

This is a small Redis/YCSB config used to validate the full path quickly in Docker.

Important detail:

- `field_length=128`

This is intentional. Redis only gives a direct hash field pointer in the path we care about when the hash is no longer encoded as `listpack`. A 128-byte field forces the YCSB hash values into `hashtable` encoding, which is the supported direct-pointer case.

## What the pointer means

### Top-level Redis string key

If the Redis key itself is a string object, `VAPTR` returns the address of the start of the string buffer.

### YCSB Redis record

YCSB Redis records are hashes, not plain strings.

For those records:

- the Redis key is something like `user6284781860667377211`
- the stored data is in fields like `field0`
- `VAPTR FIELD field0 <key>` returns the address of the **value buffer for `field0`**

So the pointer maps to the value bytes associated with the key, not to the key name bytes.

## Does it return the actual value bytes?

Yes, in the supported direct case.

Validated with a real YCSB-generated key:

- key: `user6284781860667377211`
- encoding: `hashtable`
- command:

```bash
redis-cli --raw VAPTR.DEBUG user6284781860667377211 field0 40
```

Returned:

- same address as `VAPTR`
- length `128`
- preview:

```text
#Um:[e3$>1Xo;Cm3W/->p" <,9 <9l+8|&5z"8h>
```

Then:

```bash
redis-cli --raw HGET user6284781860667377211 field0 | head -c 40
```

Returned the exact same 40-byte prefix:

```text
#Um:[e3$>1Xo;Cm3W/->p" <,9 <9l+8|&5z"8h>
```

So for that real YCSB key, the pointer resolved to the actual stored value bytes.

## End-to-end benchmark result

Ran inside the repo Docker environment with:

```bash
python python/kernmlops collect data -v \
  -c config/redis_vaptr_e2e.yaml \
  -p vaptr-e2e-fixed \
  --no-clean
```

Collection ID:

```text
vaptr-e2e-fixed-20260309T181222009662
```

Output directory:

```text
data/curated/redis/vaptr-e2e-fixed-20260309T181222009662
```

Observed output:

- `vaptr.8.parquet`
- `vaptr.9.parquet`
- `vaptr.10.parquet`
- `vaptr.11.parquet`
- `vaptr.12.parquet`
- `vaptr.13.parquet`
- `vaptr.14.parquet`
- `vaptr.end.parquet`
- `system_info.end.parquet`

Aggregated `vaptr` results:

- 660 rows
- 10 unique sampled keys

That confirms the collection path is now writing `vaptr` data end to end.

## Caveat

If Redis keeps the hash in `listpack` encoding, `VAPTR` returns unresolved.

That is deliberate. In the `listpack` case, the modules API path can materialize a temporary decoded string object, which is not the stable in-place backing storage you want for this research.

## Useful commands

Print addresses for sampled keys:

```bash
redis-cli --raw VAPTR FIELD field0 user6284781860667377211
```

Print address plus value preview:

```bash
redis-cli --raw VAPTR.DEBUG user6284781860667377211 field0 128
```

Compare directly to Redis:

```bash
redis-cli --raw HGET user6284781860667377211 field0
```

## Reproducible container verification

This is the strongest check I ran for the YCSB case that matters here.

It proves the following claim at one instant in time:

- `VAPTR.DEBUG` returns an address
- reading `len` bytes from `/proc/<redis-pid>/mem` at that address
- returns the exact same bytes as `HGET <key> <field>`

That is stronger than a preview-only check because it compares the full value, not just the first few bytes.

### Scope

This procedure verifies the direct hash-field case:

- Redis key type: `hash`
- field: `field0`
- encoding: `hashtable`

It does **not** prove anything for `listpack`, and it does **not** mean the address is permanent across updates or deletes. It proves that the address is correct for that key/field at the moment of the check.

### Container path

`/KernMLOps` is the path to this repo **inside the Docker container**.

It comes from the bind mount:

- host: `/users/SahilSC/HugePageResearch`
- container: `/KernMLOps`

So:

- on the host, use `/users/SahilSC/HugePageResearch`
- inside the container, use `/KernMLOps`

To find the newest running container, use:

```bash
docker ps --format '{{.ID}}\t{{.Names}}\t{{.RunningFor}}'
```

Then substitute that container name into the command below.

### One-shot verification command

Run this from the host:

```bash
CONTAINER="$(docker ps --format '{{.Names}}' | head -n 1)"
docker exec "$CONTAINER" bash -lc '
set -euo pipefail
cd /KernMLOps
make -C redis-module >/dev/null
python3 - <<'"'"'PY'"'"'
import subprocess
import time

ROOT = "/KernMLOps"
PORT = "6380"
KEY = "user-proof"
FIELD = "field0"
VALUE = (
    "VALUE_PROOF_"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "ABCDEFGH"
)


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


subprocess.run(
    [
        "redis-server",
        "./config/redis.conf",
        "--port",
        PORT,
        "--daemonize",
        "yes",
        "--loadmodule",
        "./redis-module/vaptr.so",
    ],
    cwd=ROOT,
    check=True,
)
time.sleep(1.0)

try:
    run("redis-cli", "-p", PORT, "DEL", KEY)
    run("redis-cli", "-p", PORT, "HSET", KEY, FIELD, VALUE)

    encoding = run("redis-cli", "-p", PORT, "OBJECT", "ENCODING", KEY).stdout.strip()
    debug_lines = run(
        "redis-cli", "-p", PORT, "--raw", "VAPTR.DEBUG", KEY, FIELD, "128"
    ).stdout.splitlines()
    hget_value = run(
        "redis-cli", "-p", PORT, "--raw", "HGET", KEY, FIELD
    ).stdout.rstrip("\n")
    info = run("redis-cli", "-p", PORT, "--raw", "INFO", "server").stdout

    if len(debug_lines) != 8:
        raise SystemExit(f"unexpected VAPTR.DEBUG output: {debug_lines!r}")

    status = debug_lines[3]
    address_text = debug_lines[5]
    length = int(debug_lines[6])
    debug_value = debug_lines[7]

    redis_pid = None
    for line in info.splitlines():
        if line.startswith("process_id:"):
            redis_pid = int(line.split(":", 1)[1])
            break
    if redis_pid is None:
        raise SystemExit("could not parse redis process id from INFO server output")

    if address_text == "(nil)":
        raise SystemExit("vaptr returned nil; this is not a direct pointer case")

    address = int(address_text, 16)
    with open(f"/proc/{redis_pid}/mem", "rb", buffering=0) as mem_file:
        mem_file.seek(address)
        proc_mem_value = mem_file.read(length).decode("utf-8")

    print("container=" + run("hostname").stdout.strip())
    print(f"redis_pid={redis_pid}")
    print(f"key={KEY}")
    print(f"field={FIELD}")
    print(f"address={address_text}")
    print(f"length={length}")
    print(f"encoding={encoding}")
    print(f"status={status}")
    print(f"debug_equals_hget={debug_value == hget_value}")
    print(f"proc_mem_equals_hget={proc_mem_value == hget_value}")
    print(f"proc_mem_equals_debug={proc_mem_value == debug_value}")
    print(f"hget={hget_value}")
    print(f"proc_mem={proc_mem_value}")
finally:
    subprocess.run(
        ["redis-cli", "-p", PORT, "SHUTDOWN", "NOSAVE"],
        text=True,
        capture_output=True,
    )
PY
'
```

### Why this works

The command forces a direct-pointer case on purpose:

- the value is 128 bytes long
- `config/redis.conf` sets `hash-max-listpack-value 64`
- that pushes the hash to `hashtable` encoding
- `vaptr` only reports an address for `hashtable`, not `listpack`

With `redis-cli --raw`, `VAPTR.DEBUG` returns an 8-element array, one value per line:

1. key
2. Redis type
3. encoding
4. `direct` or `unresolved`
5. field name
6. address
7. length
8. preview bytes

The script asks for a 128-byte preview, which equals the full value length in this test. It then independently reads those same bytes from `/proc/<redis-pid>/mem` and compares all three views of the value:

- Redis logical value from `HGET`
- `VAPTR.DEBUG` bytes
- raw process-memory bytes from `/proc/<redis-pid>/mem`

### Expected result

The exact address and PID will vary by run, but the equality checks should all be `True`.

You may also see a Redis startup warning about Transparent Huge Pages. That warning is expected in this environment and does not affect the address-verification logic.

Example from my run:

```text
container=awesome_banach
redis_pid=205160
key=user-proof
field=field0
address=0x7ffff783a143
length=128
encoding=hashtable
status=direct
debug_equals_hget=True
proc_mem_equals_hget=True
proc_mem_equals_debug=True
hget=VALUE_PROOF_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCDEFGH
proc_mem=VALUE_PROOF_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCDEFGH
```

If those three equality checks are `True`, then for that key/field at that time, the address returned by `vaptr` points to the actual Redis value bytes.

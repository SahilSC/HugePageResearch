# VAPTR Access-Bit Changes

## What changed

This update adds physical-page metadata and an interval-based access bit to
`vaptr` collection output, while keeping Redis PID ownership in `process_trace`
instead of duplicating it in the `vaptr` parquet.

The implementation is split into two layers:

1. A reusable page-access helper:
   `python/kernmlops/data_collection/page_access.py`
2. The `vaptr` hook integration:
   `python/kernmlops/data_collection/bpf_instrumentation/vaptr_hook.py`

`vaptr` now resolves the page backing each sampled virtual page, derives the
physical page frame number (PFN), and records whether that physical page was
accessed since the previous poll interval.

## Why idle-page tracking is used

The kernel does not expose a direct userspace field for the live PTE Accessed
bit in `/proc/<pid>/pagemap`. The supported userspace path for interval-based
sampling is idle-page tracking:

- writing a PFN bit to `/sys/kernel/mm/page_idle/bitmap` marks the page idle
- the kernel clears the mapped PTE Accessed bits at that point
- if the page is referenced before the next sample, the idle bit is cleared
- `access_bit` is therefore recorded as `not page_idle`

That means `access_bit` is interval-based, not an instantaneous raw PTE dump.

## Reusable access-bit lookup

The reusable helper is `PageAccessTracker`. It takes a process PID and a virtual
address, resolves the physical page, and returns:

- the mapped PFN
- the tracking PFN used for idle-page sampling
- the page-aligned physical address
- the interval `access_bit`

For quick manual use there is also:

```bash
PYTHONPATH=/KernMLOps/python/kernmlops \
python testing/get_access_bit.py --pid <pid> --va <va> --samples 3 --interval 0.2
```

Important detail:

- the first sample arms idle tracking and returns `access_bit_valid=false`
- later samples return a valid interval result

## Why PID is not duplicated in `vaptr`

`process_trace` already persists Redis process identity (`pid`, `tgid`, `name`,
lifecycle events). `vaptr` still needs a live PID internally so it can read
`/proc/<pid>/pagemap`, but that PID is now:

- discovered lazily
- cached inside the hook
- invalidated and rediscovered only if the process disappears or pagemap access fails

The PID is not written into `vaptr.end.parquet`.

## THP / compound-head behavior

When a mapped PFN is part of a compound allocation such as THP:

- `mapped_pfn` stays as the PFN returned by pagemap for the requested VA
- `tracking_pfn` is normalized to the compound-head PFN

This matters because idle-page tracking is attached to the head page for huge
pages. When `mapped_pfn != tracking_pfn`, the access-bit is being sampled at the
compound-head PFN.

## New `vaptr` columns from this change

This access-bit work adds the following columns to `vaptr`:

- `mapped_pfn`
  The PFN returned by `/proc/<pid>/pagemap` for the sampled virtual page.
- `tracking_pfn`
  The PFN actually used for idle-page tracking. This is usually the same as
  `mapped_pfn`, but can differ for THP / compound-tail mappings.
- `physical_page_addr`
  The page-aligned physical address for `mapped_pfn`, i.e. `mapped_pfn * PAGE_SIZE`.
- `tracking_physical_page_addr`
  The page-aligned physical address corresponding to `tracking_pfn`.
- `page_idle`
  The raw idle-page state read before the page is re-armed for the next interval.
  `False` means the page was referenced since the previous mark-idle step.
- `access_bit`
  Derived interval access result. This is `not page_idle` when valid.
- `access_bit_valid`
  `False` on the first observation of a PFN because that sample only arms the
  tracker. `True` once a prior interval exists to compare against.

Note:

- `available` was already present in the local working tree before this access-bit
  change; it was not added by this patch.

## Validation that was run

### Unit tests

Inside `awesome_banach`:

```bash
cd /KernMLOps
PYTHONPATH=/KernMLOps/python/kernmlops \
python -m unittest testing.test_page_access testing.test_vaptr_hook
```

Observed result:

```text
.......
----------------------------------------------------------------------
Ran 7 tests in 0.002s

OK
```

Covered behaviors:

- pagemap decoding
- idle bitmap offset/mask math
- compound-head normalization
- PFN dedupe and first-sample invalid semantics
- Redis PID cache/invalidation in the hook

### Standalone reusable lookup smoke test

Inside `awesome_banach`, a temporary Python process was started with a live
anonymous page, and `testing/get_access_bit.py` was run against its PID and VA.

Observed output:

```text
{"sample": 0, "pid": 277321, "virtual_address": "0x7ffff7fb8000", "mapped_pfn": 2274591, "tracking_pfn": 2274591, "physical_page_addr": "0x22b51f000", "tracking_physical_page_addr": "0x22b51f000", "page_idle": null, "access_bit": null, "access_bit_valid": false}
{"sample": 1, "pid": 277321, "virtual_address": "0x7ffff7fb8000", "mapped_pfn": 2274591, "tracking_pfn": 2274591, "physical_page_addr": "0x22b51f000", "tracking_physical_page_addr": "0x22b51f000", "page_idle": false, "access_bit": true, "access_bit_valid": true}
{"sample": 2, "pid": 277321, "virtual_address": "0x7ffff7fb8000", "mapped_pfn": 2274591, "tracking_pfn": 2274591, "physical_page_addr": "0x22b51f000", "tracking_physical_page_addr": "0x22b51f000", "page_idle": false, "access_bit": true, "access_bit_valid": true}
```

That confirms the reusable path works independently of `vaptr`.

### Redis end-to-end collection

Dedicated config:

- `config/redis_vaptr_access_bit_e2e.yaml`

Command run inside `awesome_banach`:

```bash
cd /KernMLOps
PYTHONPATH=/KernMLOps/python/kernmlops \
python python/kernmlops collect data -v \
  -c config/redis_vaptr_access_bit_e2e.yaml \
  -p vaptr-access-bit \
  --no-clean
```

Observed collection id:

```text
vaptr-access-bit-20260312T072231176522
```

Produced parquet files:

- `process_trace.end.parquet`
- `system_info.end.parquet`
- `vaptr.end.parquet`

Verification command:

```bash
cd /KernMLOps
PYTHONPATH=/KernMLOps/python/kernmlops \
python testing/verify_vaptr_access_bit.py \
  --run-root data/curated/redis \
  --prefix vaptr-access-bit
```

Observed verification output:

```text
run_dir=data/curated/redis/vaptr-access-bit-20260312T072231176522
vaptr_rows=1740
vaptr_valid_rows=902
vaptr_accessed_rows=902
redis_tgids=[275986]
sample_rows:
{'key': 'user6284781860667377211', 'page_addr': '0x7ffff43fb000', 'physical_page_addr': '0x35b6fe000', 'tracking_physical_page_addr': '0x35b6fe000', 'mapped_pfn': 3520254, 'tracking_pfn': 3520254, 'page_idle': False, 'access_bit': True, 'access_bit_valid': True}
```

That confirms:

- `vaptr.end.parquet` exists
- the new physical/access columns are present
- valid interval samples were captured
- at least one valid sample reported `access_bit=true`
- `process_trace` still contains the Redis TGID

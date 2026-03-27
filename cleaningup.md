# Cleanup Branch Notes

This document tracks the cleanup work on `messy-dir-cleanup`, which was branched
locally from the current `messy-dir` state so the original branch stays
untouched.

## What Changed

- added a small repo-structure pass to separate durable docs from scratch notes
- moved branch-added technical notes under `docs/research/`
- moved branch-added planning/history notes under `docs/archive/`
- reorganized reports into `reports/collector/`, `reports/kernel/`, and
  `reports/archive/`
- moved branch-added helper scripts `prepenv.sh` and `smaps.sh` under
  `scripts/archive/`
- moved the branch-added comparison image under `figures/` and aligned
  `measure_bloat.py` with that output path
- tightened `.gitignore` so it keeps both the `main` output ignores and the
  `messy-dir` local research ignores
- made BPF hook registration lazy so unit tests that only need page-access or
  VAPTR logic do not eagerly import `bcc`
- added `reports/README.md` as a simple index for the reports area
- removed obvious local scratch files:
  - `.tmp_validate.py`
  - `claudesplan.md`
  - `codexplan.md`
  - `typescript`
- removed obvious tracked clutter:
  - `smaps_output/`
  - `testing/main`
  - `hello.txt`
  - `sudo`
  - `sysctl`
  - the raw screenshot under `figures/`

## Why It Works

The main functional issue was import-time coupling. Before this pass, importing
`data_collection.page_access` or the VAPTR hook pulled in the entire BPF hook
registry, which in turn pulled perf-related schema code and its `bcc`
dependency. That made focused tests fail before they could even reach the code
under test.

The fix was to keep the same runtime behavior but defer loading:

- `python/kernmlops/data_collection/__init__.py` now resolves hook names lazily
- `python/kernmlops/data_collection/bpf_instrumentation/__init__.py` now maps
  hook names to module paths instead of importing every hook up front
- `python/kernmlops/data_schema/__init__.py` now defers perf table loading until
  the perf registry is actually requested

That keeps the collector behavior intact while making the test/import path much
less fragile.

## Tests Run

- `python -m py_compile measure_bloat.py python/kernmlops/data_collection/__init__.py python/kernmlops/data_collection/bpf_instrumentation/__init__.py`
- `python -m unittest testing.test_page_access testing.test_vaptr_hook`

Result:

- the unit tests passed after the lazy-import fix
- the syntax check for the touched Python files passed

## Issues Encountered

- `python -m py_compile python/kernmlops/data_schema/__init__.py` hit a
  permission error writing `__pycache__/__init__.cpython-312.pyc`
- rerunning with `PYTHONDONTWRITEBYTECODE=1` still hit the same stale cache
  permission path, so I relied on the successful unit-test run and the other
  syntax checks instead
- the import failures during testing were useful: they exposed that the
  remaining coupling was in `data_schema.__init__`, not in the VAPTR/page-access
  code itself

## Approaches Considered

- broad cleanup across the whole repo
- collector-only cleanup without touching import behavior
- minimal structural cleanup plus a targeted import-lazy refactor

Chosen path:

- the targeted refactor, because it fixed a real testability problem without
  turning this pass into a large architectural rewrite

## Pros And Cons

Pros:

- keeps `messy-dir` untouched by doing all work on a separate local branch
- removes obvious tracked clutter without changing the collector design
- gets branch-added notes out of the repo root without deleting the useful ones
- makes the page-access and VAPTR tests easier to run and reason about
- leaves inherited base-repo artifacts in place

Cons:

- the repo still has deeper doc sprawl and some semantic drift outside this pass
- `data_schema` is still a fairly central import hub, so a few transitive
  imports remain opinionated
- `measure_bloat.py` was only nudged to a better output path; it still uses a
  hardcoded data location and could be cleaned further later, but I reverted the
  output-path change to avoid changing inherited base-repo artifacts

## Deferred Cleanup

- normalizing config drift outside the files needed for this cleanup pass
- broader collector/module refactors that would go beyond a safe cleanup

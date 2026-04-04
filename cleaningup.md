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
- reverted the earlier lazy-import experiment for collector hooks and restored a
  `main`-style eager import layout in
  `python/kernmlops/data_collection/bpf_instrumentation/__init__.py`,
  `python/kernmlops/data_collection/__init__.py`, and
  `python/kernmlops/data_schema/__init__.py`
- installed Ubuntu's `python3-bpfcc` package and updated the local `.venv` to
  include system site-packages so the eager import path works in the repo's
  normal test environment
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

The cleanup branch briefly used lazy imports to avoid loading `bcc` during light
unit tests. That did work technically, but it made the import files feel more
complicated than the user wanted.

The current chosen approach is simpler:

- install the actual Ubuntu `bcc` bindings used by the project
- let the repo `.venv` see that system package
- keep the collector import files close to `main` so the registry remains easy
  to read

That means the eager import path is back, but the environment now satisfies it.

## Tests Run

- `sudo apt-get install -y python3-bpfcc bpfcc-tools python3.12-venv`
- `perl -0pi -e 's/include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg`
- `.venv/bin/python -m unittest testing.test_bpf_instrumentation_init testing.test_page_access testing.test_vaptr_hook`
- `.venv/bin/python - <<'PY' ... compile(...) ... PY` syntax smoke check for the
  touched Python files

Result:

- the eager-import version passed the targeted unit tests once `bcc` was
  installed and visible from `.venv`
- the import files are back to a much more direct `main`-style layout
- the local regression test now checks the registry shape instead of lazy-load
  behavior

## Issues Encountered

- the repo `.venv` originally hid system site-packages, so installing
  `python3-bpfcc` was not enough until `.venv/pyvenv.cfg` was updated
- `py_compile` still hit a stale `__pycache__` permission problem under
  `python/kernmlops/data_schema/`, so syntax validation was done with direct
  `compile(...)` calls instead

## Approaches Considered

- broad cleanup across the whole repo
- collector-only cleanup without touching import behavior
- minimal structural cleanup plus a targeted import-lazy refactor
- installing `bcc` and reverting to a `main`-style eager import layout

Chosen path:

- install `bcc` and keep the imports simple, because that matches the user's
  preference for readable registry files and avoids adding more indirection than
  the repo needs right now

## Pros And Cons

Pros:

- keeps `messy-dir` untouched by doing all work on a separate local branch
- removes obvious tracked clutter without changing the collector design
- gets branch-added notes out of the repo root without deleting the useful ones
- makes the page-access and VAPTR tests easier to run and reason about
- keeps `bpf_instrumentation.__init__.py` and related imports much closer to the
  leaner `main` branch style
- leaves inherited base-repo artifacts in place

Cons:

- the repo still has deeper doc sprawl and some semantic drift outside this pass
- the repo now depends more directly on local Ubuntu BCC packages being present
- `measure_bloat.py` was only nudged to a better output path; it still uses a
  hardcoded data location and could be cleaned further later, but I reverted the
  output-path change to avoid changing inherited base-repo artifacts

## Deferred Cleanup

- normalizing config drift outside the files needed for this cleanup pass
- broader collector/module refactors that would go beyond a safe cleanup

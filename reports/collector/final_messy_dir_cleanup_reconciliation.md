# Final `messy-dir-cleanup` Reconciliation Ledger

Date: 2026-04-04

## Goal

This report records the final audit of what still differed between
`origin/main` and the refreshed `messy-dir-cleanup` branch after merging
`origin/main` into `messy-dir-cleanup` locally at commit `e8af3b4`.

The goal is meaningful parity, not literal branch identity. A remaining diff is
acceptable if it falls into one of these buckets:

- `carry to main`: still-useful work that should land in one final PR
- `keep main version`: `main` already has the better or more reviewable version
- `intentional replacement`: the branch file was superseded by a different file
  or workflow on `main`
- `branch-local / exclude`: local notes, generated outputs, or stale branch
  history that should not land on `main`

## Summary

After the refresh merge, `36` paths still differed.

Planned carry-forward set for the final PR:

- `.gitignore`
- `reports/README.md`
- `reports/collector/task_c_runtime_validation_and_graphs.md`
- `reports/collector/task_d_vaptr_root_cause_fix.md`
- `tests/syscall_verification/HOW_THE_SYSCALL_WORKS.md`
- this reconciliation report

Everything else should either stay as the current `main` version or remain
branch-local.

## Decisions

| File or group | Decision | Reason |
| --- | --- | --- |
| `.gitignore` | `carry to main` | Add ignores for `RESEARCH.md`, old agent-plan scratch files, and `/smaps_output`, which are local/generated and should stay untracked. |
| `reports/README.md` | `carry to main` | Adds a simple index for `reports/collector/`, `reports/kernel/`, and `reports/archive/`. |
| `reports/collector/task_c_runtime_validation_and_graphs.md` | `carry to main` | Durable experiment report covering runtime validation, address instability across Redis runs, and the page-idle caveat. |
| `reports/collector/task_d_vaptr_root_cause_fix.md` | `carry to main` | Durable explanation of the VAPTR access-bit self-interference bug, the fix, and the observed before/after behavior. |
| `tests/syscall_verification/HOW_THE_SYSCALL_WORKS.md` | `carry to main` | Contributor-facing explanation of the syscall design, code path, and verifier logic. |
| `MEMORY_BLOAT_README.md` | `intentional replacement` | On `main`, setup is now documented in `SETUP.md`; keep the local resume doc off `main`. |
| `AGENTS.md`, `CLAUDE.md`, `PLANS.md`, `cleaningup.md` | `branch-local / exclude` | Local agent guidance and cleanup notes; not part of the published repo surface. |
| `config/redis.conf` | `keep main version` | Diff is only a missing trailing newline; no behavior change. |
| `config/redis_always.yaml`, `config/redis_always_compat.yaml`, `config/redis_always_det.yaml` | `keep main version` | These cleanup-branch variants repurpose baseline configs for branch-specific experiments and change benchmark semantics without a matching test-backed reason. |
| `docs/Add-Perf-Counter.md` | `keep main version` | `main` still carries this doc; the cleanup branch only deletes it. |
| `docs/archive/objectives.md`, `docs/archive/tasks.md`, `docs/install-codex-claude.md`, `docs/research/getgraphforperf.md`, `docs/research/researchprogress.md` | `branch-local / exclude` | Historical or local-environment notes, not durable published docs for the main repo. |
| `figures/vaptr_access_over_time.png`, `figures/vaptr_access_over_time_fixed.png` | `branch-local / exclude` | Generated artifacts; the reports can stand without checking these images into `main`. |
| `python/kernmlops/__init__.py` | `keep main version` | Empty placeholder on `main`; the cleanup branch only deletes it. |
| `python/kernmlops/cli/config.py` | `keep main version` | Cleanup branch adds only a duplicate import of `HugepageHarnessConfig`; no functional improvement. |
| `python/kernmlops/data_collection/bpf_instrumentation/__init__.py`, `python/kernmlops/data_collection/bpf_instrumentation/smaps_harness.py`, `python/kernmlops/data_collection/bpf_instrumentation/thp_intervention_hook.py` | `keep main version` | `main` already has a working, readable `smaps_harness` wrapper; the cleanup branch only inlines that trivial class. |
| `python/kernmlops/data_collection/bpf_instrumentation/perf/perf_hook.py` | `keep main version` | Cleanup branch simplifies perf attachment grouping, but there is no evidence that behavior is better; keep the current `main` implementation. |
| `python/kernmlops/data_schema/perf/perf_schema.py` | `keep main version` | Cleanup branch changes graph aggregation logic only; unverified and outside the core merge goal. |
| `redis-module/redismodule.h` | `keep main version` | `main` intentionally avoids vendoring this large header and uses the build/fetch path instead. |
| `reports/archive/research_handoff.md`, `reports/archive/task_a_messy_dir_last_commit.md`, `reports/archive/task_b_main_last_six_commits.md` | `branch-local / exclude` | Branch-audit and handoff history, not durable product or research docs. |
| `scripts/archive/prepenv.sh`, `scripts/archive/smaps.sh` | `branch-local / exclude` | Archived helper scripts for branch-local context, not active repo entry points. |
| `tutorial.ipynb` | `branch-local / exclude` | Notebook execution-state churn, not part of the final published reconciliation. |

## Notes On Rejected Code Diffs

Two code areas looked suspicious enough to revalidate during the refresh merge:

1. `smaps_harness`
   - The cleanup branch had deleted `smaps_harness.py` and moved the tiny wrapper
     class into `thp_intervention_hook.py`.
   - During the merge refresh this briefly broke test imports because the branch
     no longer matched the `main` registry shape.
   - Result: keep the current `main` structure in the final PR rather than
     reapplying the cleanup-side refactor.

2. `perf_hook.py`
   - The cleanup branch removed grouped perf-event attachment and replaced it
     with a simpler attach path.
   - There was no test or validation evidence showing the new behavior was
     strictly better.
   - Result: keep the current `main` implementation.

## Final PR Review Guidance

Review the final PR in this order:

1. Read this report first.
2. Confirm the carry-forward set is the right one.
3. Review the actual PR diff, which should only contain:
   - the `.gitignore` update
   - the carried docs/reports
   - no collector/runtime behavior changes

If the PR contains code or config changes outside that scope, it means the final
reconciliation branch carried more than this ledger approved.

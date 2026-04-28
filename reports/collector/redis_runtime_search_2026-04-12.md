# Redis THP Runtime Search

- branch: `redis_runtimes`
- container: `heuristic_allen`
- started: `2026-04-12T20:10:57Z`
- status: `running`
- latest stage: `stage1_seed`

## Commands

- `.venv/bin/python python/kernmlops/experiments/redis_thp_replication/runtime_search.py --container-name heuristic_allen --branch-name redis_runtimes --total-budget-hours 8.0 --stage1-hours 4.5 --max-mode-runtime-minutes 30.0 --resume-manifest /KernMLOps/temp_data_analysis/redis_runtime_search_latest_manifest.json`

## Observations

- Latest completed candidate: r45_rmw45_d5_i5_uniform_largevar (completed).

## Monitor timestamps

- `[2026-04-12T21:13:07Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_u45_d5_i5_zipfian_largevar state=running detail=alive`
- `[2026-04-12T21:33:07Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_u45_d5_i5_zipfian_largevar state=running detail=alive`
- `[2026-04-12T21:53:08Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_u45_d5_i5_uniform_const state=running detail=alive`
- `[2026-04-12T22:13:08Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_u45_d5_i5_uniform_largevar state=running detail=alive`
- `[2026-04-12T22:33:08Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_u45_d5_i5_uniform_largevar state=running detail=alive`
- `[2026-04-12T22:53:09Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r40_u40_d10_i10_zipfian_largevar state=running detail=alive`
- `[2026-04-12T23:14:40Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r40_u40_d10_i10_uniform_largevar state=running detail=alive`
- `[2026-04-12T23:35:22Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_rmw45_d5_i5_zipfian_largevar state=running detail=alive`
- `[2026-04-12T23:55:23Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_rmw45_d5_i5_zipfian_largevar state=running detail=alive`
- `[2026-04-13T00:15:23Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_rmw45_d5_i5_zipfian_largevar state=running detail=alive`
- `[2026-04-13T00:35:23Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_rmw45_d5_i5_uniform_largevar state=running detail=alive`
- `[2026-04-13T00:55:24Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_rmw45_d5_i5_uniform_largevar state=running detail=alive`
- `[2026-04-13T01:15:24Z] session=redis-runtime-search status=alive stage=stage1_seed candidate=r45_rmw45_d5_i5_uniform_largevar state=running detail=alive`

## Best attempts

candidate_name|candidate_stage|always_run_mean_s|never_run_mean_s|delta_s|delta_pct|status|rejection_reason
r45_rmw45_d5_i5_uniform_largevar|stage1_seed|594.424|611.589|17.165000000000077|0.028066234023175818|completed|
r45_rmw45_d5_i5_zipfian_largevar|stage1_seed|595.808|610.665|14.856999999999971|0.024329214872311287|completed|
r45_u45_d5_i5_uniform_largevar|stage1_seed|636.354|647.308|10.95399999999995|0.016922392431423607|completed|
r45_u45_d5_i5_zipfian_largevar|stage1_seed|644.169|649.427|5.258000000000038|0.008096368029047203|completed|

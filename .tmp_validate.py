import sys
import yaml
from pathlib import Path
sys.path.insert(0, 'python/kernmlops')
from cli.config import KernmlopsConfig
from data_collection.bpf_instrumentation import all_hooks
import data_schema

print('thp_trace_in_registry', 'thp_trace' in all_hooks)
for cfg in ['config/redis_thp_harness_v1.yaml', 'defaults.yaml']:
    print('CFG', cfg)
    try:
        c = KernmlopsConfig().merge(yaml.safe_load(Path(cfg).read_text()))
        print('merge_ok')
        print('collector_hooks', c.collector_config.generic.hooks)
        print('top_hugepage_harness_present', hasattr(c, 'hugepage_harness'))
        print('harness_cfg', c.hugepage_harness)
    except Exception as e:
        print('merge_err', type(e).__name__, e)

print('data_schema_table_types_has_thp_candidates', any(t.name() == 'thp_candidates' for t in data_schema.table_types))
print('data_schema_table_types_has_vmstat_samples', any(t.name() == 'vmstat_samples' for t in data_schema.table_types))
print('data_schema_table_types_has_process_trace', any(t.name() == 'process_trace' for t in data_schema.table_types))

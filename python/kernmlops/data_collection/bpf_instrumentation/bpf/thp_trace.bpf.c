#include <linux/mm_types.h>
#include <linux/sched.h>
#include <uapi/linux/ptrace.h>

enum zone_type {
  ZONE_DMA = 0,
  ZONE_DMA32 = 1,
  ZONE_NORMAL = 2,
  ZONE_MOVABLE = 3,
};

enum migrate_mode {
  MIGRATE_ASYNC = 0,
  MIGRATE_SYNC_LIGHT = 1,
  MIGRATE_SYNC = 2,
};

typedef struct thp_scan_event {
  u32 pid;
  u32 tgid;
  u64 ts_ns;
  u64 mm;
  u64 pfn;
  s32 writable;
  s32 referenced;
  s32 none_or_zero;
  s32 status;
  s32 unmapped;
} thp_scan_event_t;

typedef struct thp_collapse_event {
  u32 pid;
  u32 tgid;
  u64 ts_ns;
  u64 mm;
  s32 isolated;
  s32 status;
} thp_collapse_event_t;

typedef struct thp_collapse_isolate_event {
  u32 pid;
  u32 tgid;
  u64 ts_ns;
  u64 pfn;
  s32 none_or_zero;
  s32 referenced;
  s32 writable;
  s32 status;
} thp_collapse_isolate_event_t;

typedef struct thp_collapse_swapin_event {
  u32 pid;
  u32 tgid;
  u64 ts_ns;
  u64 mm;
  s32 swapped_in;
  s32 referenced;
  s32 ret;
} thp_collapse_swapin_event_t;

typedef struct thp_compaction_event {
  u32 pid;
  u32 tgid;
  u64 ts_ns;
  u32 event_id;
  s32 nid;
  s32 zone_idx;
  s32 order;
  s32 ret;
  s32 status;
  s32 sync;
  u64 start_pfn;
  u64 end_pfn;
  u64 nr_scanned;
  u64 nr_taken;
  u64 nr_migrated;
  u64 nr_failed;
  u64 considered;
  u64 defer_shift;
  s32 order_failed;
  u64 zone_start;
  u64 migrate_pfn;
  u64 free_pfn;
  u64 zone_end;
  u64 gfp_mask;
  s32 prio;
} thp_compaction_event_t;

typedef struct thp_migrate_event {
  u32 pid;
  u32 tgid;
  u64 ts_ns;
  u32 event_id;
  u64 succeeded;
  u64 failed;
  u64 thp_succeeded;
  u64 thp_failed;
  u64 thp_split;
  s32 mode;
  s32 reason;
} thp_migrate_event_t;

typedef struct thp_tlb_flush_event {
  u32 pid;
  u32 tgid;
  u64 ts_ns;
  s32 reason;
  u64 pages;
} thp_tlb_flush_event_t;

typedef struct thp_syscall_enter_state {
  u64 address;
  u64 length;
  s64 behavior;
} thp_syscall_enter_state_t;

typedef struct thp_syscall_event {
  u32 pid;
  u32 tgid;
  u64 ts_ns;
  u32 syscall_id;
  u32 phase;
  u64 address;
  u64 length;
  s64 behavior;
  s64 ret;
} thp_syscall_event_t;

BPF_PERF_OUTPUT(thp_scan_events);
BPF_PERF_OUTPUT(thp_collapse_events);
BPF_PERF_OUTPUT(thp_collapse_isolate_events);
BPF_PERF_OUTPUT(thp_collapse_swapin_events);
BPF_PERF_OUTPUT(thp_compaction_events);
BPF_PERF_OUTPUT(thp_migrate_events);
BPF_PERF_OUTPUT(thp_tlb_flush_events);
BPF_PERF_OUTPUT(thp_syscall_events);
BPF_HASH(madvise_enter_state, u64, thp_syscall_enter_state_t, 32768);
BPF_HASH(munmap_enter_state, u64, thp_syscall_enter_state_t, 32768);

// compaction event ids
#define COMPACTION_EVT_SUITABLE 1
#define COMPACTION_EVT_FINISHED 2
#define COMPACTION_EVT_BEGIN 3
#define COMPACTION_EVT_END 4
#define COMPACTION_EVT_MIGRATEPAGES 5
#define COMPACTION_EVT_ISOLATE_FREEPAGES 6
#define COMPACTION_EVT_ISOLATE_MIGRATEPAGES 7
#define COMPACTION_EVT_TRY_TO_COMPACT 8
#define COMPACTION_EVT_DEFERRED 9
#define COMPACTION_EVT_DEFER_COMPACTION 10
#define COMPACTION_EVT_KCOMPACTD_WAKE 11
#define COMPACTION_EVT_KCOMPACTD_SLEEP 12
#define COMPACTION_EVT_WAKEUP_KCOMPACTD 13

// migrate event ids
#define MIGRATE_EVT_START 1
#define MIGRATE_EVT_END 2

// syscall ids
#define SYSCALL_MADVISE 1
#define SYSCALL_MUNMAP 2

static __always_inline void fill_current_pid_tgid(u32* pid, u32* tgid) {
  u64 pid_tgid = bpf_get_current_pid_tgid();
  *pid = (u32)pid_tgid;
  *tgid = (u32)(pid_tgid >> 32);
}

static __always_inline void fill_mm_owner_pid_tgid(struct mm_struct* mm, u32* pid, u32* tgid) {
  fill_current_pid_tgid(pid, tgid);
  if (!mm) {
    return;
  }
  struct task_struct* owner = NULL;
  bpf_probe_read_kernel(&owner, sizeof(owner), &mm->owner);
  if (!owner) {
    return;
  }
  bpf_probe_read_kernel(pid, sizeof(*pid), &owner->pid);
  bpf_probe_read_kernel(tgid, sizeof(*tgid), &owner->tgid);
}

TRACEPOINT_PROBE(huge_memory, mm_khugepaged_scan_pmd) {
  thp_scan_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  fill_mm_owner_pid_tgid((struct mm_struct*)args->mm, &data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.mm = (u64)args->mm;
  data.pfn = args->pfn;
  data.writable = args->writable;
  data.referenced = args->referenced;
  data.none_or_zero = args->none_or_zero;
  data.status = args->status;
  data.unmapped = args->unmapped;
  thp_scan_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

TRACEPOINT_PROBE(huge_memory, mm_collapse_huge_page) {
  thp_collapse_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  fill_mm_owner_pid_tgid((struct mm_struct*)args->mm, &data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.mm = (u64)args->mm;
  data.isolated = args->isolated;
  data.status = args->status;
  thp_collapse_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

TRACEPOINT_PROBE(huge_memory, mm_collapse_huge_page_isolate) {
  thp_collapse_isolate_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  fill_current_pid_tgid(&data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.pfn = args->pfn;
  data.none_or_zero = args->none_or_zero;
  data.referenced = args->referenced;
  data.writable = args->writable;
  data.status = args->status;
  thp_collapse_isolate_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

TRACEPOINT_PROBE(huge_memory, mm_collapse_huge_page_swapin) {
  thp_collapse_swapin_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  fill_mm_owner_pid_tgid((struct mm_struct*)args->mm, &data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.mm = (u64)args->mm;
  data.swapped_in = args->swapped_in;
  data.referenced = args->referenced;
  data.ret = args->ret;
  thp_collapse_swapin_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

static __always_inline void submit_compaction(void* ctx, thp_compaction_event_t* data) {
  fill_current_pid_tgid(&data->pid, &data->tgid);
  data->ts_ns = bpf_ktime_get_ns();
  thp_compaction_events.perf_submit(ctx, data, sizeof(*data));
}

TRACEPOINT_PROBE(compaction, mm_compaction_suitable) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_SUITABLE;
  data.nid = args->nid;
  data.zone_idx = args->idx;
  data.order = args->order;
  data.ret = args->ret;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_finished) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_FINISHED;
  data.nid = args->nid;
  data.zone_idx = args->idx;
  data.order = args->order;
  data.ret = args->ret;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_begin) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_BEGIN;
  data.zone_start = args->zone_start;
  data.migrate_pfn = args->migrate_pfn;
  data.free_pfn = args->free_pfn;
  data.zone_end = args->zone_end;
  data.sync = args->sync;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_end) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_END;
  data.zone_start = args->zone_start;
  data.migrate_pfn = args->migrate_pfn;
  data.free_pfn = args->free_pfn;
  data.zone_end = args->zone_end;
  data.sync = args->sync;
  data.status = args->status;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_migratepages) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_MIGRATEPAGES;
  data.nr_migrated = args->nr_migrated;
  data.nr_failed = args->nr_failed;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_isolate_freepages) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_ISOLATE_FREEPAGES;
  data.start_pfn = args->start_pfn;
  data.end_pfn = args->end_pfn;
  data.nr_scanned = args->nr_scanned;
  data.nr_taken = args->nr_taken;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_isolate_migratepages) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_ISOLATE_MIGRATEPAGES;
  data.start_pfn = args->start_pfn;
  data.end_pfn = args->end_pfn;
  data.nr_scanned = args->nr_scanned;
  data.nr_taken = args->nr_taken;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_try_to_compact_pages) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_TRY_TO_COMPACT;
  data.order = args->order;
  data.gfp_mask = args->gfp_mask;
  data.prio = args->prio;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_deferred) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_DEFERRED;
  data.nid = args->nid;
  data.zone_idx = args->idx;
  data.order = args->order;
  data.considered = args->considered;
  data.defer_shift = args->defer_shift;
  data.order_failed = args->order_failed;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_defer_compaction) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_DEFER_COMPACTION;
  data.nid = args->nid;
  data.zone_idx = args->idx;
  data.order = args->order;
  data.considered = args->considered;
  data.defer_shift = args->defer_shift;
  data.order_failed = args->order_failed;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_kcompactd_wake) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_KCOMPACTD_WAKE;
  data.nid = args->nid;
  data.order = args->order;
  data.zone_idx = args->highest_zoneidx;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_kcompactd_sleep) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_KCOMPACTD_SLEEP;
  data.nid = args->nid;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(compaction, mm_compaction_wakeup_kcompactd) {
  thp_compaction_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  data.event_id = COMPACTION_EVT_WAKEUP_KCOMPACTD;
  data.nid = args->nid;
  data.order = args->order;
  data.zone_idx = args->highest_zoneidx;
  submit_compaction(args, &data);
  return 0;
}

TRACEPOINT_PROBE(migrate, mm_migrate_pages_start) {
  thp_migrate_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  fill_current_pid_tgid(&data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.event_id = MIGRATE_EVT_START;
  data.mode = args->mode;
  data.reason = args->reason;
  thp_migrate_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

TRACEPOINT_PROBE(migrate, mm_migrate_pages) {
  thp_migrate_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  fill_current_pid_tgid(&data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.event_id = MIGRATE_EVT_END;
  data.succeeded = args->succeeded;
  data.failed = args->failed;
  data.thp_succeeded = args->thp_succeeded;
  data.thp_failed = args->thp_failed;
  data.thp_split = args->thp_split;
  data.mode = args->mode;
  data.reason = args->reason;
  thp_migrate_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

TRACEPOINT_PROBE(tlb, tlb_flush) {
  thp_tlb_flush_event_t data;
  __builtin_memset(&data, 0, sizeof(data));
  fill_current_pid_tgid(&data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.reason = args->reason;
  data.pages = args->pages;
  thp_tlb_flush_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

TRACEPOINT_PROBE(syscalls, sys_enter_madvise) {
  thp_syscall_event_t data;
  thp_syscall_enter_state_t state;
  u64 key = bpf_get_current_pid_tgid();
  __builtin_memset(&data, 0, sizeof(data));
  __builtin_memset(&state, 0, sizeof(state));
  fill_current_pid_tgid(&data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.syscall_id = SYSCALL_MADVISE;
  data.phase = 0;
  data.address = args->start;
  data.length = args->len_in;
  data.behavior = args->behavior;
  data.ret = 0;
  state.address = args->start;
  state.length = args->len_in;
  state.behavior = args->behavior;
  madvise_enter_state.update(&key, &state);
  thp_syscall_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

TRACEPOINT_PROBE(syscalls, sys_exit_madvise) {
  thp_syscall_event_t data;
  thp_syscall_enter_state_t* state;
  u64 key = bpf_get_current_pid_tgid();
  __builtin_memset(&data, 0, sizeof(data));
  fill_current_pid_tgid(&data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.syscall_id = SYSCALL_MADVISE;
  data.phase = 1;
  state = madvise_enter_state.lookup(&key);
  if (state) {
    data.address = state->address;
    data.length = state->length;
    data.behavior = state->behavior;
    madvise_enter_state.delete(&key);
  } else {
    data.behavior = -1;
  }
  data.ret = args->ret;
  thp_syscall_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

TRACEPOINT_PROBE(syscalls, sys_enter_munmap) {
  thp_syscall_event_t data;
  thp_syscall_enter_state_t state;
  u64 key = bpf_get_current_pid_tgid();
  __builtin_memset(&data, 0, sizeof(data));
  __builtin_memset(&state, 0, sizeof(state));
  fill_current_pid_tgid(&data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.syscall_id = SYSCALL_MUNMAP;
  data.phase = 0;
  data.address = args->addr;
  data.length = args->len;
  data.behavior = -1;
  data.ret = 0;
  state.address = args->addr;
  state.length = args->len;
  state.behavior = -1;
  munmap_enter_state.update(&key, &state);
  thp_syscall_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

TRACEPOINT_PROBE(syscalls, sys_exit_munmap) {
  thp_syscall_event_t data;
  thp_syscall_enter_state_t* state;
  u64 key = bpf_get_current_pid_tgid();
  __builtin_memset(&data, 0, sizeof(data));
  fill_current_pid_tgid(&data.pid, &data.tgid);
  data.ts_ns = bpf_ktime_get_ns();
  data.syscall_id = SYSCALL_MUNMAP;
  data.phase = 1;
  state = munmap_enter_state.lookup(&key);
  if (state) {
    data.address = state->address;
    data.length = state->length;
    data.behavior = -1;
    munmap_enter_state.delete(&key);
  } else {
    data.behavior = -1;
  }
  data.ret = args->ret;
  thp_syscall_events.perf_submit(args, &data, sizeof(data));
  return 0;
}

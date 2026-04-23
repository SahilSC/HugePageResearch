#define _GNU_SOURCE

#include <errno.h>
#include <inttypes.h>
#include <omp.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

#define POLY UINT64_C(0x0000000000000007)
#define PERIOD INT64_C(1317624576693539401)
#define GIB_BYTES UINT64_C(1073741824)
#define MIB_BYTES UINT64_C(1048576)
#define THP_BYTES UINT64_C(2097152)
#define THP_WORDS (THP_BYTES / sizeof(uint64_t))
#define SPLIT_THP_SYSCALL_NR 462
#define SPLIT_PAGE_MAX_ATTEMPTS 2

typedef struct {
    uint64_t table_bytes;
    uint64_t table_words;
    uint64_t updates_multiplier;
    uint64_t stream_seed;
    int repeats;
    int threads;
    const char *results_path;
    const char *page_summary_out;
    const char *split_schedule;
    const char *split_events_out;
    const char *pre_split_pages;
    const char *pre_split_events_out;
} config_t;

typedef struct {
    int repeat_index;
    uint64_t start_boottime_ns;
    uint64_t end_boottime_ns;
    double runtime_s;
    double gups;
    uint64_t table_bytes;
    uint64_t updates;
    uint64_t verification_errors;
    int verification_passed;
    int threads;
    uint64_t split_events;
    uint64_t split_successes;
    uint64_t split_failures;
    uint64_t split_syscall_attempts;
    uint64_t split_max_attempts;
    double split_total_wall_ms;
    double split_max_wall_ms;
    uint64_t stream_seed;
} repeat_result_t;

typedef struct {
    uint64_t update_count;
    uint64_t first_touch_update;
    uint64_t last_touch_update;
} page_summary_t;

typedef struct {
    int repeat_index;
    uint64_t page_index;
    uint64_t break_after_page_accesses;
    char *label;
    int fired;
} split_schedule_entry_t;

typedef struct {
    split_schedule_entry_t *entries;
    size_t count;
} split_schedule_t;

typedef struct {
    uint64_t page_index;
    char *label;
} pre_split_page_entry_t;

typedef struct {
    pre_split_page_entry_t *entries;
    size_t count;
} pre_split_pages_t;

typedef struct {
    int attempts;
    int success;
    int error_errno;
    char error_text[128];
    int64_t anon_hugepages_kb_before;
    int64_t anon_hugepages_kb_after;
    uint64_t split_wall_us;
} split_event_result_t;

static volatile sig_atomic_t g_stop_requested = 0;

static void handle_signal(int signo) {
    (void)signo;
    g_stop_requested = 1;
}

static uint64_t now_boottime_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_BOOTTIME, &ts) != 0) {
        perror("clock_gettime(CLOCK_BOOTTIME)");
        exit(1);
    }
    return ((uint64_t)ts.tv_sec * UINT64_C(1000000000)) + (uint64_t)ts.tv_nsec;
}

static uint64_t now_monotonic_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        perror("clock_gettime(CLOCK_MONOTONIC)");
        exit(1);
    }
    return ((uint64_t)ts.tv_sec * UINT64_C(1000000000)) + (uint64_t)ts.tv_nsec;
}

static void usage(FILE *stream, const char *argv0) {
    fprintf(
        stream,
        "Usage: %s --results <path> [--table-size-gib N | --table-size-mib N] "
        "[--repeats N] [--updates-multiplier N] [--threads N] "
        "[--stream-seed N] [--page-summary-out <csv>] "
        "[--split-schedule <csv>] [--split-events-out <csv>] "
        "[--pre-split-pages <csv>] [--pre-split-events-out <csv>]\n",
        argv0
    );
}

static bool is_power_of_two_u64(uint64_t value) {
    return value != 0 && (value & (value - UINT64_C(1))) == 0;
}

static uint64_t hpcc_starts(int64_t n) {
    int i;
    int j;
    uint64_t m2[64];
    uint64_t temp;
    uint64_t ran;

    while (n < 0) {
        n += PERIOD;
    }
    while (n > PERIOD) {
        n -= PERIOD;
    }
    if (n == 0) {
        return UINT64_C(1);
    }

    temp = UINT64_C(1);
    for (i = 0; i < 64; ++i) {
        m2[i] = temp;
        temp = (temp << 1) ^ (((int64_t)temp < 0) ? POLY : UINT64_C(0));
        temp = (temp << 1) ^ (((int64_t)temp < 0) ? POLY : UINT64_C(0));
    }

    for (i = 62; i >= 0; --i) {
        if ((n >> i) & 1) {
            break;
        }
    }

    ran = UINT64_C(2);
    while (i > 0) {
        temp = UINT64_C(0);
        for (j = 0; j < 64; ++j) {
            if ((ran >> j) & 1) {
                temp ^= m2[j];
            }
        }
        ran = temp;
        i -= 1;
        if ((n >> i) & 1) {
            ran = (ran << 1) ^ (((int64_t)ran < 0) ? POLY : UINT64_C(0));
        }
    }

    return ran;
}

static uint64_t normalize_stream_seed(uint64_t stream_seed) {
    if (stream_seed == 0) {
        fprintf(stderr, "--stream-seed must be positive\n");
        exit(2);
    }
    return ((stream_seed - UINT64_C(1)) % (uint64_t)PERIOD) + UINT64_C(1);
}

static uint64_t hpcc_seeded_start(uint64_t start_offset, uint64_t stream_seed) {
    uint64_t normalized_seed = normalize_stream_seed(stream_seed);
    uint64_t cycle_offset = (normalized_seed - UINT64_C(1)) + (start_offset % (uint64_t)PERIOD);
    cycle_offset %= (uint64_t)PERIOD;
    return hpcc_starts((int64_t)cycle_offset);
}

static int parse_u64(const char *raw, uint64_t *out) {
    char *end = NULL;
    unsigned long long parsed = strtoull(raw, &end, 10);
    if (raw[0] == '\0' || end == NULL || *end != '\0') {
        return -1;
    }
    *out = (uint64_t)parsed;
    return 0;
}

static int parse_i32(const char *raw, int *out) {
    char *end = NULL;
    long parsed = strtol(raw, &end, 10);
    if (raw[0] == '\0' || end == NULL || *end != '\0') {
        return -1;
    }
    *out = (int)parsed;
    return 0;
}

static void strip_newline(char *line) {
    size_t len = strlen(line);
    while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
        line[len - 1] = '\0';
        len -= 1;
    }
}

static config_t parse_args(int argc, char **argv) {
    config_t cfg;
    uint64_t table_size_gib = 0;
    uint64_t table_size_mib = 0;
    int i;
    bool deterministic_mode = false;

    cfg.table_bytes = 0;
    cfg.table_words = 0;
    cfg.updates_multiplier = 4;
    cfg.stream_seed = 1;
    cfg.repeats = 4;
    cfg.threads = 0;
    cfg.results_path = NULL;
    cfg.page_summary_out = NULL;
    cfg.split_schedule = NULL;
    cfg.split_events_out = NULL;
    cfg.pre_split_pages = NULL;
    cfg.pre_split_events_out = NULL;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--results") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "--results requires a path\n");
                exit(2);
            }
            cfg.results_path = argv[++i];
            continue;
        }
        if (strcmp(argv[i], "--table-size-gib") == 0) {
            if (i + 1 >= argc || parse_u64(argv[++i], &table_size_gib) != 0) {
                fprintf(stderr, "--table-size-gib requires an integer\n");
                exit(2);
            }
            continue;
        }
        if (strcmp(argv[i], "--table-size-mib") == 0) {
            if (i + 1 >= argc || parse_u64(argv[++i], &table_size_mib) != 0) {
                fprintf(stderr, "--table-size-mib requires an integer\n");
                exit(2);
            }
            continue;
        }
        if (strcmp(argv[i], "--repeats") == 0) {
            if (i + 1 >= argc || parse_i32(argv[++i], &cfg.repeats) != 0) {
                fprintf(stderr, "--repeats requires an integer\n");
                exit(2);
            }
            continue;
        }
        if (strcmp(argv[i], "--updates-multiplier") == 0) {
            if (i + 1 >= argc || parse_u64(argv[++i], &cfg.updates_multiplier) != 0) {
                fprintf(stderr, "--updates-multiplier requires an integer\n");
                exit(2);
            }
            continue;
        }
        if (strcmp(argv[i], "--threads") == 0) {
            if (i + 1 >= argc || parse_i32(argv[++i], &cfg.threads) != 0) {
                fprintf(stderr, "--threads requires an integer\n");
                exit(2);
            }
            continue;
        }
        if (strcmp(argv[i], "--stream-seed") == 0) {
            if (i + 1 >= argc || parse_u64(argv[++i], &cfg.stream_seed) != 0) {
                fprintf(stderr, "--stream-seed requires an integer\n");
                exit(2);
            }
            continue;
        }
        if (strcmp(argv[i], "--page-summary-out") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "--page-summary-out requires a path\n");
                exit(2);
            }
            cfg.page_summary_out = argv[++i];
            deterministic_mode = true;
            continue;
        }
        if (strcmp(argv[i], "--split-schedule") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "--split-schedule requires a path\n");
                exit(2);
            }
            cfg.split_schedule = argv[++i];
            deterministic_mode = true;
            continue;
        }
        if (strcmp(argv[i], "--split-events-out") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "--split-events-out requires a path\n");
                exit(2);
            }
            cfg.split_events_out = argv[++i];
            deterministic_mode = true;
            continue;
        }
        if (strcmp(argv[i], "--pre-split-pages") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "--pre-split-pages requires a path\n");
                exit(2);
            }
            cfg.pre_split_pages = argv[++i];
            deterministic_mode = true;
            continue;
        }
        if (strcmp(argv[i], "--pre-split-events-out") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "--pre-split-events-out requires a path\n");
                exit(2);
            }
            cfg.pre_split_events_out = argv[++i];
            deterministic_mode = true;
            continue;
        }
        if (strcmp(argv[i], "--help") == 0) {
            usage(stdout, argv[0]);
            exit(0);
        }
        fprintf(stderr, "Unknown argument: %s\n", argv[i]);
        usage(stderr, argv[0]);
        exit(2);
    }

    if (cfg.results_path == NULL) {
        fprintf(stderr, "--results is required\n");
        usage(stderr, argv[0]);
        exit(2);
    }
    if ((table_size_gib == 0 && table_size_mib == 0) ||
        (table_size_gib != 0 && table_size_mib != 0)) {
        fprintf(stderr, "Specify exactly one of --table-size-gib or --table-size-mib\n");
        exit(2);
    }
    if (cfg.repeats <= 0) {
        fprintf(stderr, "--repeats must be positive\n");
        exit(2);
    }
    if (cfg.updates_multiplier == 0) {
        fprintf(stderr, "--updates-multiplier must be positive\n");
        exit(2);
    }
    if (cfg.threads < 0) {
        fprintf(stderr, "--threads must be non-negative\n");
        exit(2);
    }
    if (cfg.pre_split_events_out != NULL && cfg.pre_split_pages == NULL) {
        fprintf(stderr, "--pre-split-events-out requires --pre-split-pages\n");
        exit(2);
    }
    if (deterministic_mode && cfg.threads != 1) {
        fprintf(
            stderr,
            "Deterministic page-summary and split modes require --threads 1\n"
        );
        exit(2);
    }

    cfg.stream_seed = normalize_stream_seed(cfg.stream_seed);

    if (table_size_gib != 0) {
        cfg.table_bytes = table_size_gib * GIB_BYTES;
    } else {
        cfg.table_bytes = table_size_mib * MIB_BYTES;
    }

    if (cfg.table_bytes < sizeof(uint64_t) ||
        (cfg.table_bytes % sizeof(uint64_t)) != 0) {
        fprintf(stderr, "table size must be a multiple of 8 bytes\n");
        exit(2);
    }
    cfg.table_words = cfg.table_bytes / sizeof(uint64_t);
    if (!is_power_of_two_u64(cfg.table_words)) {
        fprintf(stderr, "table words must be a power of two\n");
        exit(2);
    }

    return cfg;
}

static void initialize_table(uint64_t *table, uint64_t table_words) {
    uint64_t i;
#pragma omp parallel for schedule(static)
    for (i = 0; i < table_words; ++i) {
        table[i] = i;
    }
}

static uint64_t random_access_update_parallel(
    uint64_t table_words,
    uint64_t updates,
    uint64_t *table,
    uint64_t stream_seed
) {
    uint64_t completed_updates = 0;

#pragma omp parallel reduction(+ : completed_updates)
    {
        int thread_index = omp_get_thread_num();
        int thread_count = omp_get_num_threads();
        uint64_t base_updates = updates / (uint64_t)thread_count;
        uint64_t remainder = updates % (uint64_t)thread_count;
        uint64_t thread_updates = base_updates + ((uint64_t)thread_index < remainder ? UINT64_C(1) : UINT64_C(0));
        uint64_t start_offset = (base_updates * (uint64_t)thread_index) +
            (((uint64_t)thread_index < remainder) ? (uint64_t)thread_index : remainder);
        uint64_t ran = hpcc_seeded_start(start_offset, stream_seed);
        uint64_t local_completed = 0;

        while (local_completed < thread_updates) {
            if (g_stop_requested) {
                break;
            }
            ran = (ran << 1) ^ (((int64_t)ran < 0) ? POLY : UINT64_C(0));
            table[ran & (table_words - UINT64_C(1))] ^= ran;
            local_completed += UINT64_C(1);
        }

        completed_updates += local_completed;
    }

    return completed_updates;
}

static int64_t read_self_anon_hugepages_kb(void) {
    FILE *rollup = fopen("/proc/self/smaps_rollup", "r");
    char line[256];
    int64_t parsed = -1;

    if (rollup == NULL) {
        return -1;
    }

    while (fgets(line, sizeof(line), rollup) != NULL) {
        long long value = 0;
        if (sscanf(line, "AnonHugePages:%lld kB", &value) == 1) {
            parsed = (int64_t)value;
            break;
        }
    }

    fclose(rollup);
    return parsed;
}

static void capture_errno_text(int error_number, char *buffer, size_t buffer_size) {
    if (buffer_size == 0) {
        return;
    }
    if (error_number == 0) {
        buffer[0] = '\0';
        return;
    }

#if defined(__GLIBC__) && defined(_GNU_SOURCE)
    {
        char *message = strerror_r(error_number, buffer, buffer_size);
        if (message != buffer) {
            snprintf(buffer, buffer_size, "%s", message);
        }
    }
#else
    if (strerror_r(error_number, buffer, buffer_size) != 0) {
        snprintf(buffer, buffer_size, "errno=%d", error_number);
    }
#endif
}

static int invoke_split_thp_syscall(pid_t pid, uint64_t vaddr) {
    long rc = syscall(SPLIT_THP_SYSCALL_NR, (long)pid, (unsigned long)vaddr);
    if (rc == 0) {
        return 0;
    }
    return errno != 0 ? errno : EIO;
}

static split_event_result_t split_table_page(pid_t pid, uint64_t vaddr) {
    split_event_result_t result;
    uint64_t start_ns = now_monotonic_ns();
    int attempt;

    result.attempts = 0;
    result.success = 0;
    result.error_errno = 0;
    result.error_text[0] = '\0';
    result.anon_hugepages_kb_before = read_self_anon_hugepages_kb();
    result.anon_hugepages_kb_after = result.anon_hugepages_kb_before;
    result.split_wall_us = 0;

    for (attempt = 1; attempt <= SPLIT_PAGE_MAX_ATTEMPTS; ++attempt) {
        int split_error = invoke_split_thp_syscall(pid, vaddr);
        result.attempts = attempt;
        if (split_error == 0) {
            result.success = 1;
            result.error_errno = 0;
            result.error_text[0] = '\0';
            break;
        }
        result.error_errno = split_error;
        capture_errno_text(split_error, result.error_text, sizeof(result.error_text));
    }

    result.anon_hugepages_kb_after = read_self_anon_hugepages_kb();
    result.split_wall_us = (now_monotonic_ns() - start_ns) / UINT64_C(1000);
    return result;
}

static void csv_write_escaped(FILE *stream, const char *raw) {
    const char *cursor = raw;
    fputc('"', stream);
    while (*cursor != '\0') {
        if (*cursor == '"') {
            fputc('"', stream);
        }
        fputc(*cursor, stream);
        cursor += 1;
    }
    fputc('"', stream);
}

static void write_page_summary_header(FILE *stream) {
    fprintf(
        stream,
        "repeat_index,page_index,start_offset_bytes,update_count,first_touch_update,last_touch_update\n"
    );
}

static void write_split_events_header(FILE *stream) {
    fprintf(
        stream,
        "repeat_index,page_index,label,page_access_count,global_update_index,split_vaddr_hex,attempts,success,error_errno,error_text,anon_hugepages_kb_before,anon_hugepages_kb_after,split_wall_us\n"
    );
}

static void write_pre_split_events_header(FILE *stream) {
    fprintf(
        stream,
        "repeat_index,page_index,label,split_vaddr_hex,attempts,success,error_errno,error_text,anon_hugepages_kb_before,anon_hugepages_kb_after,split_wall_us\n"
    );
}

static void free_split_schedule(split_schedule_t *schedule) {
    size_t index;
    for (index = 0; index < schedule->count; ++index) {
        free(schedule->entries[index].label);
    }
    free(schedule->entries);
    schedule->entries = NULL;
    schedule->count = 0;
}

static void free_pre_split_pages(pre_split_pages_t *pages) {
    size_t index;
    for (index = 0; index < pages->count; ++index) {
        free(pages->entries[index].label);
    }
    free(pages->entries);
    pages->entries = NULL;
    pages->count = 0;
}

static void append_split_schedule_entry(
    split_schedule_t *schedule,
    split_schedule_entry_t entry
) {
    size_t new_count = schedule->count + 1;
    split_schedule_entry_t *resized = realloc(
        schedule->entries,
        new_count * sizeof(split_schedule_entry_t)
    );
    if (resized == NULL) {
        perror("realloc split schedule");
        free_split_schedule(schedule);
        exit(1);
    }
    resized[schedule->count] = entry;
    schedule->entries = resized;
    schedule->count = new_count;
}

static void append_pre_split_page_entry(
    pre_split_pages_t *pages,
    pre_split_page_entry_t entry
) {
    size_t new_count = pages->count + 1;
    pre_split_page_entry_t *resized = realloc(
        pages->entries,
        new_count * sizeof(pre_split_page_entry_t)
    );
    if (resized == NULL) {
        perror("realloc pre-split pages");
        free_pre_split_pages(pages);
        exit(1);
    }
    resized[pages->count] = entry;
    pages->entries = resized;
    pages->count = new_count;
}

static split_schedule_t load_split_schedule(
    const char *path,
    uint64_t page_count,
    int repeats
) {
    FILE *stream = fopen(path, "r");
    split_schedule_t schedule;
    char *line = NULL;
    size_t line_cap = 0;
    ssize_t line_len;
    bool saw_header = false;

    schedule.entries = NULL;
    schedule.count = 0;

    if (stream == NULL) {
        fprintf(stderr, "failed to open split schedule '%s': %s\n", path, strerror(errno));
        exit(1);
    }

    while ((line_len = getline(&line, &line_cap, stream)) != -1) {
        char *field1;
        char *field2;
        char *field3;
        char *field4;
        char *comma1;
        char *comma2;
        char *comma3;
        split_schedule_entry_t entry;

        if (line_len == 0) {
            continue;
        }
        strip_newline(line);
        if (line[0] == '\0') {
            continue;
        }
        if (!saw_header) {
            if (strcmp(line, "repeat_index,page_index,break_after_page_accesses,label") != 0) {
                fprintf(stderr, "unexpected split schedule header: %s\n", line);
                free(line);
                fclose(stream);
                free_split_schedule(&schedule);
                exit(1);
            }
            saw_header = true;
            continue;
        }

        field1 = line;
        comma1 = strchr(field1, ',');
        if (comma1 == NULL) {
            fprintf(stderr, "malformed split schedule row: %s\n", line);
            free(line);
            fclose(stream);
            free_split_schedule(&schedule);
            exit(1);
        }
        *comma1 = '\0';
        field2 = comma1 + 1;

        comma2 = strchr(field2, ',');
        if (comma2 == NULL) {
            fprintf(stderr, "malformed split schedule row: %s\n", line);
            free(line);
            fclose(stream);
            free_split_schedule(&schedule);
            exit(1);
        }
        *comma2 = '\0';
        field3 = comma2 + 1;

        comma3 = strchr(field3, ',');
        if (comma3 == NULL) {
            fprintf(stderr, "malformed split schedule row: %s\n", line);
            free(line);
            fclose(stream);
            free_split_schedule(&schedule);
            exit(1);
        }
        *comma3 = '\0';
        field4 = comma3 + 1;

        if (parse_i32(field1, &entry.repeat_index) != 0 ||
            parse_u64(field2, &entry.page_index) != 0 ||
            parse_u64(field3, &entry.break_after_page_accesses) != 0) {
            fprintf(stderr, "malformed numeric split schedule row: %s,%s,%s\n", field1, field2, field3);
            free(line);
            fclose(stream);
            free_split_schedule(&schedule);
            exit(1);
        }
        if (entry.repeat_index < 0 || entry.repeat_index >= repeats) {
            fprintf(
                stderr,
                "split schedule repeat_index %d is outside [0, %d)\n",
                entry.repeat_index,
                repeats
            );
            free(line);
            fclose(stream);
            free_split_schedule(&schedule);
            exit(1);
        }
        if (entry.page_index >= page_count) {
            fprintf(
                stderr,
                "split schedule page_index %" PRIu64 " is outside page_count %" PRIu64 "\n",
                entry.page_index,
                page_count
            );
            free(line);
            fclose(stream);
            free_split_schedule(&schedule);
            exit(1);
        }
        entry.label = strdup(field4);
        if (entry.label == NULL) {
            perror("strdup split schedule label");
            free(line);
            fclose(stream);
            free_split_schedule(&schedule);
            exit(1);
        }
        entry.fired = 0;
        append_split_schedule_entry(&schedule, entry);
    }

    free(line);
    fclose(stream);

    if (!saw_header) {
        fprintf(stderr, "split schedule '%s' is missing the CSV header\n", path);
        free_split_schedule(&schedule);
        exit(1);
    }

    return schedule;
}

static pre_split_pages_t load_pre_split_pages(
    const char *path,
    uint64_t page_count
) {
    FILE *stream = fopen(path, "r");
    pre_split_pages_t pages;
    char *line = NULL;
    size_t line_cap = 0;
    ssize_t line_len;
    bool saw_header = false;

    pages.entries = NULL;
    pages.count = 0;

    if (stream == NULL) {
        fprintf(stderr, "failed to open pre-split pages '%s': %s\n", path, strerror(errno));
        exit(1);
    }

    while ((line_len = getline(&line, &line_cap, stream)) != -1) {
        char *field1;
        char *field2;
        char *comma1;
        pre_split_page_entry_t entry;

        if (line_len == 0) {
            continue;
        }
        strip_newline(line);
        if (line[0] == '\0') {
            continue;
        }
        if (!saw_header) {
            if (strcmp(line, "page_index,label") != 0) {
                fprintf(stderr, "unexpected pre-split pages header: %s\n", line);
                free(line);
                fclose(stream);
                free_pre_split_pages(&pages);
                exit(1);
            }
            saw_header = true;
            continue;
        }

        field1 = line;
        comma1 = strchr(field1, ',');
        if (comma1 == NULL) {
            fprintf(stderr, "malformed pre-split pages row: %s\n", line);
            free(line);
            fclose(stream);
            free_pre_split_pages(&pages);
            exit(1);
        }
        *comma1 = '\0';
        field2 = comma1 + 1;

        if (parse_u64(field1, &entry.page_index) != 0) {
            fprintf(stderr, "malformed pre-split page_index: %s\n", field1);
            free(line);
            fclose(stream);
            free_pre_split_pages(&pages);
            exit(1);
        }
        if (entry.page_index >= page_count) {
            fprintf(
                stderr,
                "pre-split page_index %" PRIu64 " is outside page_count %" PRIu64 "\n",
                entry.page_index,
                page_count
            );
            free(line);
            fclose(stream);
            free_pre_split_pages(&pages);
            exit(1);
        }
        entry.label = strdup(field2);
        if (entry.label == NULL) {
            perror("strdup pre-split page label");
            free(line);
            fclose(stream);
            free_pre_split_pages(&pages);
            exit(1);
        }
        append_pre_split_page_entry(&pages, entry);
    }

    free(line);
    fclose(stream);

    if (!saw_header) {
        fprintf(stderr, "pre-split pages '%s' is missing the CSV header\n", path);
        free_pre_split_pages(&pages);
        exit(1);
    }
    if (pages.count == 0) {
        fprintf(stderr, "pre-split pages '%s' did not contain any page rows\n", path);
        free_pre_split_pages(&pages);
        exit(1);
    }

    return pages;
}

static void accumulate_split_result(
    repeat_result_t *result,
    const split_event_result_t *split_event
) {
    double split_wall_ms = (double)split_event->split_wall_us / 1000.0;

    result->split_events += UINT64_C(1);
    result->split_syscall_attempts += (uint64_t)split_event->attempts;
    if ((uint64_t)split_event->attempts > result->split_max_attempts) {
        result->split_max_attempts = (uint64_t)split_event->attempts;
    }
    result->split_total_wall_ms += split_wall_ms;
    if (split_wall_ms > result->split_max_wall_ms) {
        result->split_max_wall_ms = split_wall_ms;
    }
    if (split_event->success) {
        result->split_successes += UINT64_C(1);
    } else {
        result->split_failures += UINT64_C(1);
    }
}

static void write_pre_split_event_row(
    FILE *stream,
    int repeat_index,
    const pre_split_page_entry_t *entry,
    uint64_t split_vaddr,
    const split_event_result_t *split_event
) {
    fprintf(
        stream,
        "%d,%" PRIu64 ",",
        repeat_index,
        entry->page_index
    );
    csv_write_escaped(stream, entry->label);
    fprintf(
        stream,
        ",0x%016" PRIx64 ",%d,%d,%d,",
        split_vaddr,
        split_event->attempts,
        split_event->success,
        split_event->error_errno
    );
    csv_write_escaped(stream, split_event->error_text);
    fprintf(
        stream,
        ",%" PRId64 ",%" PRId64 ",%" PRIu64 "\n",
        split_event->anon_hugepages_kb_before,
        split_event->anon_hugepages_kb_after,
        split_event->split_wall_us
    );
}

static void pre_split_table_pages(
    uint64_t *table,
    const pre_split_pages_t *pages,
    FILE *pre_split_events_stream,
    int repeat_index,
    repeat_result_t *result
) {
    size_t entry_index;
    pid_t pid = getpid();

    for (entry_index = 0; entry_index < pages->count; ++entry_index) {
        const pre_split_page_entry_t *entry = &pages->entries[entry_index];
        uint64_t split_vaddr = (uint64_t)((uintptr_t)table) + (entry->page_index * THP_BYTES);
        split_event_result_t split_event = split_table_page(pid, split_vaddr);

        accumulate_split_result(result, &split_event);

        if (pre_split_events_stream != NULL) {
            write_pre_split_event_row(
                pre_split_events_stream,
                repeat_index,
                entry,
                split_vaddr,
                &split_event
            );
            fflush(pre_split_events_stream);
        }
    }
}

static void reset_page_summary(page_summary_t *summaries, uint64_t page_count) {
    uint64_t page_index;
    for (page_index = 0; page_index < page_count; ++page_index) {
        summaries[page_index].update_count = 0;
        summaries[page_index].first_touch_update = UINT64_MAX;
        summaries[page_index].last_touch_update = UINT64_MAX;
    }
}

static void write_page_summary_rows(
    FILE *stream,
    int repeat_index,
    const page_summary_t *summaries,
    uint64_t page_count
) {
    uint64_t page_index;
    for (page_index = 0; page_index < page_count; ++page_index) {
        const page_summary_t *summary = &summaries[page_index];
        fprintf(
            stream,
            "%d,%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRId64 ",%" PRId64 "\n",
            repeat_index,
            page_index,
            page_index * THP_BYTES,
            summary->update_count,
            summary->update_count == 0 ? INT64_C(-1) : (int64_t)summary->first_touch_update,
            summary->update_count == 0 ? INT64_C(-1) : (int64_t)summary->last_touch_update
        );
    }
    fflush(stream);
}

static uint64_t random_access_update_instrumented(
    const config_t *cfg,
    int repeat_index,
    uint64_t updates,
    uint64_t *table,
    page_summary_t *summaries,
    uint64_t page_count,
    split_schedule_t *schedule,
    FILE *split_events_stream,
    repeat_result_t *result
) {
    uint64_t *page_access_counts = calloc(page_count, sizeof(uint64_t));
    uint64_t ran = hpcc_seeded_start(0, cfg->stream_seed);
    uint64_t completed_updates = 0;
    uint64_t global_update_index;

    if (page_access_counts == NULL) {
        perror("calloc page_access_counts");
        exit(1);
    }

    if (summaries != NULL) {
        reset_page_summary(summaries, page_count);
    }

    for (global_update_index = 0; global_update_index < updates; ++global_update_index) {
        uint64_t word_index;
        uint64_t byte_offset;
        uint64_t page_index;
        uint64_t page_access_count;
        size_t schedule_index;

        if (g_stop_requested) {
            break;
        }

        ran = (ran << 1) ^ (((int64_t)ran < 0) ? POLY : UINT64_C(0));
        word_index = ran & (cfg->table_words - UINT64_C(1));
        byte_offset = word_index * sizeof(uint64_t);
        page_index = byte_offset / THP_BYTES;
        page_access_count = page_access_counts[page_index];

        for (schedule_index = 0; schedule_index < schedule->count; ++schedule_index) {
            split_schedule_entry_t *entry = &schedule->entries[schedule_index];
            split_event_result_t split_event;
            uint64_t split_vaddr;

            if (entry->fired) {
                continue;
            }
            if (entry->repeat_index != repeat_index) {
                continue;
            }
            if (entry->page_index != page_index) {
                continue;
            }
            if (entry->break_after_page_accesses != page_access_count) {
                continue;
            }

            entry->fired = 1;
            split_vaddr = (uint64_t)((uintptr_t)table) + (page_index * THP_BYTES);
            split_event = split_table_page(getpid(), split_vaddr);

            accumulate_split_result(result, &split_event);

            if (split_events_stream != NULL) {
                fprintf(
                    split_events_stream,
                    "%d,%" PRIu64 ",",
                    repeat_index,
                    page_index
                );
                csv_write_escaped(split_events_stream, entry->label);
                fprintf(
                    split_events_stream,
                    ",%" PRIu64 ",%" PRIu64 ",0x%016" PRIx64 ",%d,%d,%d,",
                    page_access_count,
                    global_update_index,
                    split_vaddr,
                    split_event.attempts,
                    split_event.success,
                    split_event.error_errno
                );
                csv_write_escaped(split_events_stream, split_event.error_text);
                fprintf(
                    split_events_stream,
                    ",%" PRId64 ",%" PRId64 ",%" PRIu64 "\n",
                    split_event.anon_hugepages_kb_before,
                    split_event.anon_hugepages_kb_after,
                    split_event.split_wall_us
                );
                fflush(split_events_stream);
            }
        }

        table[word_index] ^= ran;
        if (summaries != NULL) {
            page_summary_t *summary = &summaries[page_index];
            summary->update_count += UINT64_C(1);
            if (summary->first_touch_update == UINT64_MAX) {
                summary->first_touch_update = global_update_index;
            }
            summary->last_touch_update = global_update_index;
        }
        page_access_counts[page_index] += UINT64_C(1);
        completed_updates += UINT64_C(1);
    }

    free(page_access_counts);
    return completed_updates;
}

static uint64_t count_table_errors(uint64_t table_words, uint64_t *table) {
    uint64_t i;
    uint64_t errors = 0;

#pragma omp parallel for reduction(+ : errors) schedule(static)
    for (i = 0; i < table_words; ++i) {
        if (table[i] != i) {
            errors += 1;
        }
    }

    return errors;
}

static void write_result(FILE *results, const repeat_result_t *result) {
    fprintf(
        results,
        "{\"repeat_index\":%d,"
        "\"start_boottime_ns\":%" PRIu64 ","
        "\"end_boottime_ns\":%" PRIu64 ","
        "\"runtime_s\":%.9f,"
        "\"gups\":%.9f,"
        "\"table_bytes\":%" PRIu64 ","
        "\"updates\":%" PRIu64 ","
        "\"verification_errors\":%" PRIu64 ","
        "\"verification_passed\":%s,"
        "\"threads\":%d,"
        "\"split_events\":%" PRIu64 ","
        "\"split_successes\":%" PRIu64 ","
        "\"split_failures\":%" PRIu64 ","
        "\"split_syscall_attempts\":%" PRIu64 ","
        "\"split_max_attempts\":%" PRIu64 ","
        "\"split_total_wall_ms\":%.3f,"
        "\"split_max_wall_ms\":%.3f,"
        "\"stream_seed\":%" PRIu64 "}\n",
        result->repeat_index,
        result->start_boottime_ns,
        result->end_boottime_ns,
        result->runtime_s,
        result->gups,
        result->table_bytes,
        result->updates,
        result->verification_errors,
        result->verification_passed ? "true" : "false",
        result->threads,
        result->split_events,
        result->split_successes,
        result->split_failures,
        result->split_syscall_attempts,
        result->split_max_attempts,
        result->split_total_wall_ms,
        result->split_max_wall_ms,
        result->stream_seed
    );
    fflush(results);
}

int main(int argc, char **argv) {
    config_t cfg = parse_args(argc, argv);
    repeat_result_t result;
    FILE *results = NULL;
    FILE *page_summary_stream = NULL;
    FILE *split_events_stream = NULL;
    FILE *pre_split_events_stream = NULL;
    uint64_t *table = NULL;
    uint64_t updates = cfg.table_words * cfg.updates_multiplier;
    uint64_t page_count = (cfg.table_bytes + THP_BYTES - UINT64_C(1)) / THP_BYTES;
    page_summary_t *page_summaries = NULL;
    split_schedule_t split_schedule = {0};
    pre_split_pages_t pre_split_pages = {0};
    int repeat_index;
    int failures = 0;
    bool instrumented_mode = false;

    (void)prctl(PR_SET_NAME, "gups", 0, 0, 0);
    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    if (cfg.threads > 0) {
        omp_set_num_threads(cfg.threads);
    }

    instrumented_mode = cfg.page_summary_out != NULL || cfg.split_schedule != NULL;

    printf(
        "gups: table_bytes=%" PRIu64 " table_words=%" PRIu64
        " repeats=%d updates_multiplier=%" PRIu64
        " threads=%d stream_seed=%" PRIu64 "\n",
        cfg.table_bytes,
        cfg.table_words,
        cfg.repeats,
        cfg.updates_multiplier,
        (cfg.threads > 0) ? cfg.threads : omp_get_max_threads(),
        cfg.stream_seed
    );

    table = mmap(
        NULL,
        (size_t)cfg.table_bytes,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS,
        -1,
        0
    );
    if (table == MAP_FAILED) {
        fprintf(stderr, "mmap failed: %s\n", strerror(errno));
        return 1;
    }

    results = fopen(cfg.results_path, "w");
    if (results == NULL) {
        fprintf(stderr, "failed to open results file '%s': %s\n", cfg.results_path, strerror(errno));
        munmap(table, (size_t)cfg.table_bytes);
        return 1;
    }

    if (cfg.page_summary_out != NULL) {
        page_summary_stream = fopen(cfg.page_summary_out, "w");
        if (page_summary_stream == NULL) {
            fprintf(
                stderr,
                "failed to open page summary file '%s': %s\n",
                cfg.page_summary_out,
                strerror(errno)
            );
            fclose(results);
            munmap(table, (size_t)cfg.table_bytes);
            return 1;
        }
        write_page_summary_header(page_summary_stream);
        page_summaries = calloc(page_count, sizeof(page_summary_t));
        if (page_summaries == NULL) {
            perror("calloc page_summaries");
            fclose(page_summary_stream);
            fclose(results);
            munmap(table, (size_t)cfg.table_bytes);
            return 1;
        }
    }

    if (cfg.split_events_out != NULL) {
        split_events_stream = fopen(cfg.split_events_out, "w");
        if (split_events_stream == NULL) {
            fprintf(
                stderr,
                "failed to open split events file '%s': %s\n",
                cfg.split_events_out,
                strerror(errno)
            );
            free(page_summaries);
            if (page_summary_stream != NULL) {
                fclose(page_summary_stream);
            }
            fclose(results);
            munmap(table, (size_t)cfg.table_bytes);
            return 1;
        }
        write_split_events_header(split_events_stream);
    }

    if (cfg.pre_split_events_out != NULL) {
        pre_split_events_stream = fopen(cfg.pre_split_events_out, "w");
        if (pre_split_events_stream == NULL) {
            fprintf(
                stderr,
                "failed to open pre-split events file '%s': %s\n",
                cfg.pre_split_events_out,
                strerror(errno)
            );
            if (split_events_stream != NULL) {
                fclose(split_events_stream);
            }
            free(page_summaries);
            if (page_summary_stream != NULL) {
                fclose(page_summary_stream);
            }
            fclose(results);
            munmap(table, (size_t)cfg.table_bytes);
            return 1;
        }
        write_pre_split_events_header(pre_split_events_stream);
    }

    if (cfg.split_schedule != NULL) {
        split_schedule = load_split_schedule(cfg.split_schedule, page_count, cfg.repeats);
    }
    if (cfg.pre_split_pages != NULL) {
        pre_split_pages = load_pre_split_pages(cfg.pre_split_pages, page_count);
    }

    for (repeat_index = 0; repeat_index < cfg.repeats; ++repeat_index) {
        uint64_t completed_updates;
        uint64_t errors;
        uint64_t start_ns;
        uint64_t end_ns;
        double runtime_s;
        int verify_threshold_passed;

        if (g_stop_requested) {
            fprintf(stderr, "stop requested before repeat %d\n", repeat_index);
            break;
        }

        initialize_table(table, cfg.table_words);

        memset(&result, 0, sizeof(result));
        result.repeat_index = repeat_index;
        result.table_bytes = cfg.table_bytes;
        result.updates = updates;
        result.stream_seed = cfg.stream_seed;
        result.threads = (cfg.threads > 0) ? cfg.threads : omp_get_max_threads();

        if (pre_split_pages.count > 0) {
            pre_split_table_pages(
                table,
                &pre_split_pages,
                pre_split_events_stream,
                repeat_index,
                &result
            );
        }

        start_ns = now_boottime_ns();
        if (instrumented_mode) {
            completed_updates = random_access_update_instrumented(
                &cfg,
                repeat_index,
                updates,
                table,
                page_summaries,
                page_count,
                &split_schedule,
                split_events_stream,
                &result
            );
        } else {
            completed_updates = random_access_update_parallel(
                cfg.table_words,
                updates,
                table,
                cfg.stream_seed
            );
        }
        end_ns = now_boottime_ns();

        if (completed_updates != updates) {
            fprintf(
                stderr,
                "benchmark interrupted during repeat %d after %" PRIu64 " of %" PRIu64 " updates\n",
                repeat_index,
                completed_updates,
                updates
            );
            failures += 1;
            break;
        }

        runtime_s = (double)(end_ns - start_ns) / 1000000000.0;
        completed_updates = random_access_update_parallel(
            cfg.table_words,
            updates,
            table,
            cfg.stream_seed
        );
        if (completed_updates != updates) {
            fprintf(
                stderr,
                "verification interrupted during repeat %d after %" PRIu64 " of %" PRIu64 " updates\n",
                repeat_index,
                completed_updates,
                updates
            );
            failures += 1;
            break;
        }
        errors = count_table_errors(cfg.table_words, table);
        verify_threshold_passed = (errors <= (cfg.table_words / UINT64_C(100)));
        if (!verify_threshold_passed) {
            failures += 1;
        }

        result.start_boottime_ns = start_ns;
        result.end_boottime_ns = end_ns;
        result.runtime_s = runtime_s;
        result.gups = (runtime_s > 0.0)
            ? ((double)updates / runtime_s) / 1000000000.0
            : 0.0;
        result.verification_errors = errors;
        result.verification_passed = verify_threshold_passed;

        if (page_summary_stream != NULL && page_summaries != NULL) {
            write_page_summary_rows(
                page_summary_stream,
                repeat_index,
                page_summaries,
                page_count
            );
        }

        write_result(results, &result);

        printf(
            "repeat=%d runtime_s=%.6f gups=%.6f verification_passed=%s verification_errors=%" PRIu64
            " split_events=%" PRIu64 " split_successes=%" PRIu64 " split_failures=%" PRIu64 "\n",
            repeat_index,
            result.runtime_s,
            result.gups,
            result.verification_passed ? "true" : "false",
            result.verification_errors,
            result.split_events,
            result.split_successes,
            result.split_failures
        );
        fflush(stdout);
    }

    free_split_schedule(&split_schedule);
    free_pre_split_pages(&pre_split_pages);
    if (pre_split_events_stream != NULL) {
        fclose(pre_split_events_stream);
    }
    if (split_events_stream != NULL) {
        fclose(split_events_stream);
    }
    if (page_summary_stream != NULL) {
        fclose(page_summary_stream);
    }
    free(page_summaries);
    fclose(results);
    if (munmap(table, (size_t)cfg.table_bytes) != 0) {
        fprintf(stderr, "munmap failed: %s\n", strerror(errno));
        return 1;
    }

    return failures == 0 ? 0 : 1;
}

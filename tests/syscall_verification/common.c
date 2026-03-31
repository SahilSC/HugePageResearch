#include "common.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

static unsigned long align_up(unsigned long value, unsigned long align)
{
    return (value + align - 1) & ~(align - 1);
}

struct syscall_result invoke_split_thp(pid_t pid, unsigned long vaddr)
{
    struct syscall_result result;

    errno = 0;
    result.rc = syscall(__NR_split_thp, pid, vaddr);
    result.err = errno;
    return result;
}

int create_aligned_mapping(char **mapping_out, size_t *length_out)
{
    void *reservation;
    unsigned long base;
    unsigned long aligned;
    size_t prefix;
    size_t suffix;
    const size_t reservation_len = 3UL * THP_SIZE;

    reservation = mmap(NULL, reservation_len, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (reservation == MAP_FAILED) {
        return -1;
    }

    base = (unsigned long) reservation;
    aligned = align_up(base, THP_SIZE);
    prefix = aligned - base;
    suffix = (base + reservation_len) - (aligned + THP_SIZE);

    if (prefix != 0 && munmap((void *) base, prefix) != 0) {
        munmap(reservation, reservation_len);
        return -1;
    }

    if (suffix != 0 && munmap((void *) (aligned + THP_SIZE), suffix) != 0) {
        munmap((void *) aligned, THP_SIZE);
        return -1;
    }

    *mapping_out = (char *) aligned;
    *length_out = THP_SIZE;
    return 0;
}

void fill_pattern(char *mapping, size_t length, unsigned int seed)
{
    size_t i;

    for (i = 0; i < length; ++i) {
        mapping[i] = (char) ((i * 131U + seed) & 0xffU);
    }
}

int verify_pattern(const char *mapping, size_t length, unsigned int seed)
{
    size_t i;

    for (i = 0; i < length; ++i) {
        unsigned char expected = (unsigned char) ((i * 131U + seed) & 0xffU);
        if ((unsigned char) mapping[i] != expected) {
            fprintf(stderr,
                    "pattern mismatch at offset %zu: got 0x%02x expected 0x%02x\n",
                    i, (unsigned char) mapping[i], expected);
            return -1;
        }
    }

    return 0;
}

int collapse_to_thp(char *mapping, size_t length)
{
    struct mapping_info info;

    if (madvise(mapping, length, MADV_HUGEPAGE) != 0) {
        return -1;
    }

    if (madvise(mapping, length, MADV_COLLAPSE) != 0) {
        return -1;
    }

    if (read_mapping_info(getpid(), mapping, &info) != 0) {
        return -1;
    }

    if (info.anon_huge_kb < (THP_SIZE / 1024UL)) {
        errno = EIO;
        return -1;
    }

    return 0;
}

int read_mapping_info(pid_t pid, const void *address, struct mapping_info *info_out)
{
    char path[64];
    char line[512];
    FILE *fp;
    unsigned long target;
    int active = 0;
    struct mapping_info info = {0};

    snprintf(path, sizeof(path), "/proc/%ld/smaps", (long) pid);
    fp = fopen(path, "r");
    if (fp == NULL) {
        return -1;
    }

    target = (unsigned long) address;

    while (fgets(line, sizeof(line), fp) != NULL) {
        unsigned long start;
        unsigned long end;

        if (sscanf(line, "%lx-%lx", &start, &end) == 2) {
            if (active) {
                break;
            }

            if (target >= start && target < end) {
                info.start = start;
                info.end = end;
                info.found = 1;
                active = 1;
            }
            continue;
        }

        if (!active) {
            continue;
        }

        if (sscanf(line, "Size: %zu kB", &info.size_kb) == 1) {
            continue;
        }
        if (sscanf(line, "AnonHugePages: %zu kB", &info.anon_huge_kb) == 1) {
            continue;
        }
        if (sscanf(line, "KernelPageSize: %zu kB", &info.kernel_page_size_kb) == 1) {
            continue;
        }
        if (sscanf(line, "MMUPageSize: %zu kB", &info.mmu_page_size_kb) == 1) {
            continue;
        }
    }

    fclose(fp);

    if (!info.found) {
        errno = ENOENT;
        return -1;
    }

    *info_out = info;
    return 0;
}

void print_mapping_info(FILE *stream, const struct mapping_info *info)
{
    fprintf(stream, "start=0x%lx\n", info->start);
    fprintf(stream, "end=0x%lx\n", info->end);
    fprintf(stream, "size_kb=%zu\n", info->size_kb);
    fprintf(stream, "anon_huge_pages_kb=%zu\n", info->anon_huge_kb);
    fprintf(stream, "kernel_page_size_kb=%zu\n", info->kernel_page_size_kb);
    fprintf(stream, "mmu_page_size_kb=%zu\n", info->mmu_page_size_kb);
    fprintf(stream, "contains_thp=%s\n",
            info->anon_huge_kb >= (THP_SIZE / 1024UL) ? "yes" : "no");
}

int mapping_is_thp(pid_t pid, const void *address)
{
    struct mapping_info info;

    if (read_mapping_info(pid, address, &info) != 0) {
        return 0;
    }

    return info.anon_huge_kb >= (THP_SIZE / 1024UL);
}

int read_ptrace_scope(int *value_out)
{
    FILE *fp = fopen("/proc/sys/kernel/yama/ptrace_scope", "r");

    if (fp == NULL) {
        return -1;
    }

    if (fscanf(fp, "%d", value_out) != 1) {
        fclose(fp);
        errno = EINVAL;
        return -1;
    }

    fclose(fp);
    return 0;
}

int expect_success_result(const char *label, struct syscall_result result)
{
    if (result.rc == 0) {
        return 0;
    }

    fprintf(stderr, "%s: expected success, rc=%ld errno=%d (%s)\n",
            label, result.rc, result.err, strerror(result.err));
    return -1;
}

int expect_errno_result(const char *label, struct syscall_result result, int expected)
{
    if (result.rc == -1 && result.err == expected) {
        return 0;
    }

    fprintf(stderr,
            "%s: expected errno=%d (%s), got rc=%ld errno=%d (%s)\n",
            label, expected, strerror(expected), result.rc,
            result.err, strerror(result.err));
    return -1;
}

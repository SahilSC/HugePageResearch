#ifndef TESTS_SYSCALL_VERIFICATION_COMMON_H
#define TESTS_SYSCALL_VERIFICATION_COMMON_H

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/types.h>

#define THP_SIZE (2UL * 1024UL * 1024UL)

#ifndef MADV_COLLAPSE
#define MADV_COLLAPSE 25
#endif

#ifndef __NR_split_thp
#define __NR_split_thp 462
#endif

struct syscall_result {
    long rc;
    int err;
};

struct mapping_info {
    unsigned long start;
    unsigned long end;
    size_t size_kb;
    size_t anon_huge_kb;
    size_t kernel_page_size_kb;
    size_t mmu_page_size_kb;
    int found;
};

struct syscall_result invoke_split_thp(pid_t pid, unsigned long vaddr);
int create_aligned_mapping(char **mapping_out, size_t *length_out);
void fill_pattern(char *mapping, size_t length, unsigned int seed);
int verify_pattern(const char *mapping, size_t length, unsigned int seed);
int collapse_to_thp(char *mapping, size_t length);
int read_mapping_info(pid_t pid, const void *address, struct mapping_info *info_out);
void print_mapping_info(FILE *stream, const struct mapping_info *info);
int mapping_is_thp(pid_t pid, const void *address);
int read_ptrace_scope(int *value_out);
int expect_success_result(const char *label, struct syscall_result result);
int expect_errno_result(const char *label, struct syscall_result result, int expected);

#endif

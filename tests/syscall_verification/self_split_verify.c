#include "common.h"

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int main(void)
{
    char *thp_mapping = NULL;
    char *regular_mapping = NULL;
    size_t thp_length = 0;
    struct mapping_info info;
    struct syscall_result result;
    const unsigned int seed = 0x5aU;
    int rc = 1;

    if (create_aligned_mapping(&thp_mapping, &thp_length) != 0) {
        perror("create_aligned_mapping");
        return 1;
    }

    fill_pattern(thp_mapping, thp_length, seed);

    if (collapse_to_thp(thp_mapping, thp_length) != 0) {
        perror("collapse_to_thp");
        goto out;
    }

    if (read_mapping_info(getpid(), thp_mapping, &info) != 0) {
        perror("read_mapping_info");
        goto out;
    }

    printf("before split:\n");
    print_mapping_info(stdout, &info);

    result = invoke_split_thp(getpid(), (unsigned long) thp_mapping + 4096UL);
    if (result.rc == -1 && result.err == ENOSYS) {
        fprintf(stderr, "split_thp syscall is not available on the running kernel\n");
        rc = 77;
        goto out;
    }
    if (expect_success_result("self split", result) != 0) {
        goto out;
    }

    if (read_mapping_info(getpid(), thp_mapping, &info) != 0) {
        perror("read_mapping_info");
        goto out;
    }

    printf("after split:\n");
    print_mapping_info(stdout, &info);

    if (mapping_is_thp(getpid(), thp_mapping)) {
        fprintf(stderr, "mapping still reports THP after split\n");
        goto out;
    }

    if (verify_pattern(thp_mapping, thp_length, seed) != 0) {
        goto out;
    }

    result = invoke_split_thp(getpid(), (unsigned long) thp_mapping + 8192UL);
    if (expect_errno_result("second split on same mapping", result, ENOENT) != 0) {
        goto out;
    }

    regular_mapping = mmap(NULL, 65536, PROT_READ | PROT_WRITE,
                           MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (regular_mapping == MAP_FAILED) {
        regular_mapping = NULL;
        perror("mmap regular mapping");
        goto out;
    }
    memset(regular_mapping, 0x11, 65536);

    result = invoke_split_thp(getpid(), (unsigned long) regular_mapping);
    if (expect_errno_result("non-THP mapping", result, ENOENT) != 0) {
        goto out;
    }

    if (munmap(regular_mapping, 65536) != 0) {
        perror("munmap regular mapping");
        goto out;
    }
    regular_mapping = NULL;

    result = invoke_split_thp(getpid(), 0UL);
    if (expect_errno_result("unmapped address", result, EFAULT) != 0) {
        goto out;
    }

    result = invoke_split_thp((pid_t) INT_MAX, (unsigned long) thp_mapping);
    if (expect_errno_result("bad pid", result, ESRCH) != 0) {
        goto out;
    }

    printf("self_split_verify: PASS\n");
    rc = 0;

out:
    if (regular_mapping != NULL) {
        munmap(regular_mapping, 65536);
    }
    if (thp_mapping != NULL) {
        munmap(thp_mapping, thp_length);
    }
    return rc;
}

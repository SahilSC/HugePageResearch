#include "common.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int parse_pid(const char *text, pid_t *pid_out)
{
    char *end = NULL;
    long value = strtol(text, &end, 0);

    if (end == text || *end != '\0' || value <= 0) {
        return -1;
    }

    *pid_out = (pid_t) value;
    return 0;
}

static int parse_address(const char *text, unsigned long *addr_out)
{
    char *end = NULL;
    unsigned long value = strtoul(text, &end, 0);

    if (end == text || *end != '\0') {
        return -1;
    }

    *addr_out = value;
    return 0;
}

int main(int argc, char **argv)
{
    pid_t pid;
    unsigned long addr;
    struct syscall_result result;

    if (argc != 3) {
        fprintf(stderr, "usage: %s <pid> <vaddr>\n", argv[0]);
        return 2;
    }

    if (parse_pid(argv[1], &pid) != 0 || parse_address(argv[2], &addr) != 0) {
        fprintf(stderr, "invalid pid or virtual address\n");
        return 2;
    }

    result = invoke_split_thp(pid, addr);
    if (result.rc == 0) {
        printf("split_thp(pid=%ld, vaddr=0x%lx) succeeded\n", (long) pid, addr);
        return 0;
    }

    fprintf(stderr, "split_thp(pid=%ld, vaddr=0x%lx) failed: errno=%d (%s)\n",
            (long) pid, addr, result.err, strerror(result.err));
    if (result.err == ENOSYS) {
        fprintf(stderr, "running kernel does not have the custom split_thp syscall yet\n");
    }
    return 1;
}

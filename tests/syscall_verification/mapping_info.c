#include "common.h"

#include <stdio.h>
#include <stdlib.h>

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
    struct mapping_info info;

    if (argc != 3) {
        fprintf(stderr, "usage: %s <pid> <vaddr>\n", argv[0]);
        return 2;
    }

    if (parse_pid(argv[1], &pid) != 0 || parse_address(argv[2], &addr) != 0) {
        fprintf(stderr, "invalid pid or virtual address\n");
        return 2;
    }

    if (read_mapping_info(pid, (void *) addr, &info) != 0) {
        perror("read_mapping_info");
        return 1;
    }

    print_mapping_info(stdout, &info);
    return 0;
}

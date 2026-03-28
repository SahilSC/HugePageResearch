#include "common.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

struct child_payload {
    unsigned long thp_addr;
    unsigned long regular_addr;
    int status;
    int err;
};

static int write_all(int fd, const void *buf, size_t count)
{
    const char *cursor = buf;

    while (count > 0) {
        ssize_t written = write(fd, cursor, count);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        cursor += (size_t) written;
        count -= (size_t) written;
    }

    return 0;
}

static int read_all(int fd, void *buf, size_t count)
{
    char *cursor = buf;

    while (count > 0) {
        ssize_t got = read(fd, cursor, count);
        if (got == 0) {
            errno = EPIPE;
            return -1;
        }
        if (got < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        cursor += (size_t) got;
        count -= (size_t) got;
    }

    return 0;
}

static int child_main(int parent_write_fd)
{
    char *thp_mapping = NULL;
    char *regular_mapping = NULL;
    size_t thp_length = 0;
    struct child_payload payload = {0};
    const unsigned int seed = 0x33U;

    if (create_aligned_mapping(&thp_mapping, &thp_length) != 0) {
        payload.err = errno;
        goto send_ready;
    }
    fill_pattern(thp_mapping, thp_length, seed);

    if (collapse_to_thp(thp_mapping, thp_length) != 0) {
        payload.err = errno;
        goto send_ready;
    }

    regular_mapping = mmap(NULL, 65536, PROT_READ | PROT_WRITE,
                           MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (regular_mapping == MAP_FAILED) {
        regular_mapping = NULL;
        payload.err = errno;
        goto send_ready;
    }
    memset(regular_mapping, 0x7f, 65536);

    payload.thp_addr = (unsigned long) thp_mapping;
    payload.regular_addr = (unsigned long) regular_mapping;
    payload.status = 1;

send_ready:
    if (write_all(parent_write_fd, &payload, sizeof(payload)) != 0) {
        _exit(1);
    }

    if (payload.status == 0) {
        _exit(1);
    }

    for (;;) {
        char command;
        int verify_ok = 0;

        if (read_all(STDIN_FILENO, &command, sizeof(command)) != 0) {
            _exit(1);
        }

        if (command == 'V') {
            verify_ok = verify_pattern(thp_mapping, thp_length, seed) == 0;
            if (write_all(parent_write_fd, &verify_ok, sizeof(verify_ok)) != 0) {
                _exit(1);
            }
            continue;
        }

        if (command == 'Q') {
            _exit(0);
        }

        _exit(2);
    }
}

int main(void)
{
    int child_to_parent[2];
    int parent_to_child[2];
    pid_t child_pid;
    struct child_payload payload;
    struct mapping_info info;
    struct syscall_result result;
    int child_verify_ok = 0;
    int status = 0;
    int rc = 1;
    char command;
    pid_t target_pid = -1;

    if (pipe(child_to_parent) != 0 || pipe(parent_to_child) != 0) {
        perror("pipe");
        return 1;
    }

    child_pid = fork();
    if (child_pid < 0) {
        perror("fork");
        return 1;
    }

    if (child_pid == 0) {
        close(child_to_parent[0]);
        close(parent_to_child[1]);
        if (dup2(parent_to_child[0], STDIN_FILENO) < 0) {
            _exit(1);
        }
        close(parent_to_child[0]);
        return child_main(child_to_parent[1]);
    }

    close(child_to_parent[1]);
    close(parent_to_child[0]);

    if (read_all(child_to_parent[0], &payload, sizeof(payload)) != 0) {
        perror("read child payload");
        goto out;
    }

    if (!payload.status) {
        errno = payload.err;
        perror("child setup");
        goto out;
    }
    target_pid = child_pid;

    if (read_mapping_info(child_pid, (void *) payload.thp_addr, &info) != 0) {
        perror("read_mapping_info before split");
        goto out;
    }
    printf("child before split:\n");
    print_mapping_info(stdout, &info);

    result = invoke_split_thp(child_pid, payload.thp_addr + 4096UL);
    if (result.rc == -1 && result.err == ENOSYS) {
        fprintf(stderr, "split_thp syscall is not available on the running kernel\n");
        rc = 77;
        goto out;
    }
    if (expect_success_result("cross-process split", result) != 0) {
        goto out;
    }

    if (read_mapping_info(child_pid, (void *) payload.thp_addr, &info) != 0) {
        perror("read_mapping_info after split");
        goto out;
    }
    printf("child after split:\n");
    print_mapping_info(stdout, &info);

    if (mapping_is_thp(child_pid, (void *) payload.thp_addr)) {
        fprintf(stderr, "child mapping still reports THP after split\n");
        goto out;
    }

    result = invoke_split_thp(child_pid, payload.thp_addr + 8192UL);
    if (expect_errno_result("second child split on same mapping", result, ENOENT) != 0) {
        goto out;
    }

    result = invoke_split_thp(child_pid, payload.regular_addr);
    if (expect_errno_result("child non-THP mapping", result, ENOENT) != 0) {
        goto out;
    }

    result = invoke_split_thp(child_pid, 0UL);
    if (expect_errno_result("child unmapped address", result, EFAULT) != 0) {
        goto out;
    }

    command = 'V';
    if (write_all(parent_to_child[1], &command, sizeof(command)) != 0) {
        perror("write verify command");
        goto out;
    }
    if (read_all(child_to_parent[0], &child_verify_ok, sizeof(child_verify_ok)) != 0) {
        perror("read verify result");
        goto out;
    }
    if (!child_verify_ok) {
        fprintf(stderr, "child reported data mismatch after split\n");
        goto out;
    }

    command = 'Q';
    if (write_all(parent_to_child[1], &command, sizeof(command)) != 0) {
        perror("write quit command");
        goto out;
    }

    if (waitpid(child_pid, &status, 0) < 0) {
        perror("waitpid");
        goto out;
    }
    child_pid = -1;

    result = invoke_split_thp(target_pid, payload.thp_addr);
    if (expect_errno_result("dead target pid", result, ESRCH) != 0) {
        goto out;
    }

    printf("cross_process_split_verify: PASS\n");
    rc = 0;

out:
    if (child_pid > 0) {
        close(parent_to_child[1]);
        waitpid(child_pid, NULL, 0);
    }
    close(child_to_parent[0]);
    if (child_pid <= 0) {
        close(parent_to_child[1]);
    }
    return rc;
}

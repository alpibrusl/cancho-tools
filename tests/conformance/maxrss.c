/* maxrss -- run a program and print its own peak resident memory in KB.
 *
 * A process forked from the test runner inherits the runner's resident-set
 * high-water mark: Linux keeps the larger of the old and new address
 * space's peak across exec, so a tool started straight from Python reports
 * Python's ~24 MB whatever it does itself. This launcher is small, so the
 * child it forks starts from a mark of about a megabyte, and wait4 answers
 * that child's own peak. (test_memory.py's first version measured the
 * runner, not the tool: every peak came back 24,448 KB.)
 */
#include <stdio.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>
#include <fcntl.h>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: maxrss program [args...]\n");
        return 2;
    }
    pid_t pid = fork();
    if (pid == 0) {
        int null = open("/dev/null", O_WRONLY);
        dup2(null, 1);
        execv(argv[1], argv + 1);
        _exit(127);
    }
    int status;
    struct rusage usage;
    if (wait4(pid, &status, 0, &usage) < 0) {
        return 3;
    }
    printf("%ld %d\n", usage.ru_maxrss, WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status));
    return 0;
}

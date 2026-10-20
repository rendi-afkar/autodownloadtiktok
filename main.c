#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <limits.h>
#include <libgen.h>

int main(int argc, char **argv) {
    char exe[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (n < 0) {
        perror("readlink");
        return 1;
    }
    exe[n] = '\0';

    char basedir[PATH_MAX];
    char core[PATH_MAX];
    char source[PATH_MAX];

    snprintf(basedir, sizeof(basedir), "%s", dirname(exe));
    snprintf(source, sizeof(source), "%s/lib/core.py", basedir);
    snprintf(core, sizeof(core), "%s/lib/core.pyc", basedir);

    if (access(source, R_OK) == 0) {
        snprintf(core, sizeof(core), "%s", source);
    }

    if (access(core, R_OK) != 0) {
        fprintf(stderr, "[X] Source dan bytecode core tidak ditemukan.\n");
        return 1;
    }

    char **args = calloc((size_t)argc + 3, sizeof(char *));
    if (!args) {
        perror("calloc");
        return 1;
    }

    args[0] = "python";
    args[1] = core;

    for (int i = 1; i < argc; i++) {
        args[i + 1] = argv[i];
    }

    execvp("python", args);
    perror("python");
    free(args);
    return 1;
}

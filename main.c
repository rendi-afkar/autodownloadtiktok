#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <libgen.h>
#include <limits.h>

int main(int argc, char *argv[]) {
    char exe[PATH_MAX], script[PATH_MAX + 32];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (n < 0) { perror("readlink"); return 1; }
    exe[n] = '\0';
    char *dir = dirname(exe);
    if (chdir(dir) != 0) { perror("chdir"); return 1; }
    snprintf(script, sizeof(script), "%s/lib/core.pyc", dir);
    char **args = malloc((argc + 2) * sizeof(char *));
    args[0] = "python";
    args[1] = script;
    for (int i = 1; i < argc; i++) args[i + 1] = argv[i];
    args[argc + 1] = NULL;
    execvp("python", args);
    perror("python");
    return 1;
}

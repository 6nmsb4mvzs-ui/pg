// PG3D Mac Client: the app bundle's main executable.
//
// launch.sh does all the setup (install, Python runtime, server). It then hands back
// how to start the window, and this process runs it by loading Python itself. The
// window therefore lives in the app's own process: macOS shows the client's name and
// icon, and there is no separate "Python" app in the Dock.
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern int _NSGetExecutablePath(char *buf, uint32_t *size);

enum { kLib, kHome, kPath, kPython, kScript, kUrl, kPidfile, kLog, kFields };

static int read_handoff(const char *file, char fields[kFields][PATH_MAX]) {
    FILE *f = fopen(file, "r");
    if (f == NULL) return 0;
    int n = 0;
    while (n < kFields && fgets(fields[n], PATH_MAX, f) != NULL) {
        fields[n][strcspn(fields[n], "\n")] = '\0';
        n++;
    }
    fclose(f);
    return n == kFields;
}

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, real) == NULL) return 1;
    char *slash = strrchr(real, '/');  // .../Contents/MacOS/<exe>
    if (slash == NULL) return 1;
    *slash = '\0';
    char script[PATH_MAX + 32];
    if (snprintf(script, sizeof(script), "%s/../Resources/app/launch.sh", real) >= (int)sizeof(script)) return 1;

    const char *tmp = getenv("TMPDIR");
    char handoff[PATH_MAX + 32];
    if (snprintf(handoff, sizeof(handoff), "%s/pg3d-window-%d", tmp && *tmp ? tmp : "/tmp", (int)getpid()) >=
        (int)sizeof(handoff)) return 1;
    unlink(handoff);
    setenv("PG3D_HANDOFF", handoff, 1);

    pid_t child = fork();
    if (child < 0) return 1;
    if (child == 0) {
        char **args = calloc((size_t)argc + 2, sizeof(char *));
        if (args == NULL) _exit(127);
        args[0] = "zsh";
        args[1] = script;
        for (int i = 1; i < argc; i++) args[i + 1] = argv[i];
        execv("/bin/zsh", args);
        _exit(127);
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {}
    unsetenv("PG3D_HANDOFF");

    static char f[kFields][PATH_MAX];
    if (!read_handoff(handoff, f)) {
        unlink(handoff);
        // Nothing to open here (already running, an error dialog was shown, or the
        // installed copy was opened instead).
        return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
    }
    unlink(handoff);

    int log = open(f[kLog], O_WRONLY | O_APPEND | O_CREAT, 0644);
    if (log >= 0) {
        dup2(log, 1);
        dup2(log, 2);
        close(log);
    }
    char *window_argv[] = {f[kPython], f[kScript], f[kUrl], f[kPidfile], NULL};

    setenv("PYTHONHOME", f[kHome], 1);
    setenv("PYTHONPATH", f[kPath], 1);
    setenv("PG3D_EMBEDDED", "1", 1);
    void *lib = dlopen(f[kLib], RTLD_NOW | RTLD_GLOBAL);
    int (*bytes_main)(int, char **) = lib ? (int (*)(int, char **))dlsym(lib, "Py_BytesMain") : NULL;
    if (bytes_main != NULL) {
        return bytes_main(4, window_argv);
    }
    fprintf(stderr, "[launcher] could not load %s (%s); starting the window with %s instead.\n",
            f[kLib], lib ? "no Py_BytesMain" : dlerror(), f[kPython]);
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONPATH");
    unsetenv("PG3D_EMBEDDED");
    execv(f[kPython], window_argv);
    return 1;
}

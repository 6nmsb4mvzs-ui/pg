// Detach the game into its own session before exec, preserving Steam/DYLD env.
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 6) {
        fprintf(stderr, "Expected executable, dylib, FPS, log path, observe flag.\n");
        return 2;
    }
    int handshake[2];
    if (pipe(handshake) != 0) { perror("pipe"); return 1; }
    if (fcntl(handshake[1], F_SETFD, FD_CLOEXEC) < 0) { perror("fcntl"); return 1; }
    pid_t child = fork();
    if (child < 0) { perror("fork"); return 1; }
    if (child == 0) {
        close(handshake[0]);
        int error = 0;
        if (setsid() < 0) { error = errno; goto fail; }
        signal(SIGHUP, SIG_IGN);
        int input = open("/dev/null", O_RDONLY);
        int output = open(argv[4], O_WRONLY | O_APPEND | O_CREAT, 0600);
        if (input < 0 || output < 0) { error = errno; goto fail; }
        if (dup2(input, STDIN_FILENO) < 0 || dup2(output, STDOUT_FILENO) < 0 ||
            dup2(output, STDERR_FILENO) < 0) { error = errno; goto fail; }
        close(input);
        close(output);
        if (setenv("DYLD_INSERT_LIBRARIES", argv[2], 1) ||
            setenv("PG3D_UNLOCK_FPS", argv[3], 1) ||
            setenv("PG3D_UNLOCK_LOG", argv[4], 1) ||
            setenv("PG3D_UNLOCK_OBSERVE", argv[5], 1) ||
            setenv("SteamAppId", "2524890", 1) ||
            setenv("SteamGameId", "2524890", 1)) { error = errno; goto fail; }
        // Unity reads this Boolean during startup, before IL2CPP is ready.
        // Its default display-link blit path capped Metal acquisition at 60 Hz
        // on the tested ProMotion Mac even with an uncapped engine frame loop.
        int observe = strcmp(argv[5], "1") == 0;
        if (observe ? unsetenv("UNITY_DISPLAYLINK_BLIT") :
                      setenv("UNITY_DISPLAYLINK_BLIT", "0", 1)) {
            error = errno;
            goto fail;
        }
        dprintf(STDERR_FILENO, "[Launcher] UNITY_DISPLAYLINK_BLIT=%s; %s.\n",
                observe ? "unset" : "0",
                observe ? "original display-link behavior" : "60 Hz display-link blit path disabled");
        execl(argv[1], argv[1], (char *)NULL);
        error = errno;
    fail:
        (void)write(handshake[1], &error, sizeof(error));
        _exit(1);
    }
    close(handshake[1]);
    int error = 0;
    ssize_t count;
    do { count = read(handshake[0], &error, sizeof(error)); } while (count < 0 && errno == EINTR);
    close(handshake[0]);
    if (count != 0) {
        if (count > 0) errno = error;
        perror("launch game");
        (void)waitpid(child, NULL, 0);
        return 1;
    }
    printf("%d\n", child);
    return 0;
}

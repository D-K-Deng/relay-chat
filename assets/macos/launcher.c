/* Finder entry point. The interpreter and the Relay source live inside the app. */
#include <libgen.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    uint32_t length = 0;
    _NSGetExecutablePath(NULL, &length);
    char *self = malloc(length);
    if (_NSGetExecutablePath(self, &length) != 0) return 1;

    char *directory = dirname(self);
    char *python, *script;
    asprintf(&python, "%s/../Resources/runtime/bin/python3.12", directory);
    asprintf(&script, "%s/../Resources/app/app.py", directory);
    if (!getenv("SSL_CERT_FILE")) {
        char *certs = "/etc/ssl/cert.pem";
        if (access(certs, R_OK) != 0)
            asprintf(&certs, "%s/../Resources/runtime/lib/python3.12/site-packages/pip/_vendor/certifi/cacert.pem", directory);
        setenv("SSL_CERT_FILE", certs, 0);
    }

    /* Keep Python's user site and host environment out of the bundled runtime. */
    char **args = calloc(argc + 5, sizeof(*args));
    args[0] = python;
    args[1] = "-E";
    args[2] = "-s";
    args[3] = "-B";
    args[4] = script;
    int count = 5;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) != 0) args[count++] = argv[i];
    }
    /* Release Finder's application instance. Opening it again can reopen the window. */
    if (count == 5) {
        pid_t child = fork();
        if (child < 0) { perror("RelayChat"); return 1; }
        if (child > 0) return 0;
        setsid();
    }
    execv(python, args);
    perror("RelayChat");
    return 1;
}

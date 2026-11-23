/* Finder entry point. The interpreter and the Relay source live inside the app. */
#include <libgen.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wchar.h>

int main(int argc, char **argv) {
    uint32_t length = 0;
    _NSGetExecutablePath(NULL, &length);
    char *self = malloc(length);
    if (_NSGetExecutablePath(self, &length) != 0) return 1;

    char *directory = dirname(self);
    char *runtime, *library, *python, *script;
    asprintf(&runtime, "%s/../Resources/runtime", directory);
    asprintf(&library, "%s/lib/libpython3.12.dylib", runtime);
    asprintf(&python, "%s/bin/python3.12", runtime);
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
    /* Keep Finder's bundle identity and Dock icon while running the embedded Python. */
    void *lib = dlopen(library, RTLD_NOW | RTLD_GLOBAL);
    if (!lib) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    wchar_t *(*decode)(const char *, size_t *) = dlsym(lib, "Py_DecodeLocale");
    void (*set_home)(const wchar_t *) = dlsym(lib, "Py_SetPythonHome");
    int (*run)(int, char **) = dlsym(lib, "Py_BytesMain");
    set_home(decode(runtime, NULL));
    return run(count, args);
}

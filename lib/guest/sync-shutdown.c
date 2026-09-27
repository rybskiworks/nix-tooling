#define _DEFAULT_SOURCE
#include <unistd.h>

/* Container userspace skips systemd's disk flush, but this guest owns a VM disk.
 * Shutdown hooks run after services and remaining processes have stopped. */
int main(void) {
    sync();
    return 0;
}

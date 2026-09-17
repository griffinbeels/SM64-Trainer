/* Parity host for practice_rom.h: one 128-hex-digit ROM header per stdin line,
 * one "0"/"1" per stdout line. tests/test_practice_rom.py compares the answers
 * with src/sm64_events/core/onboarding.py over the same headers. */
#include <stdio.h>
#include <string.h>
#include "practice_rom.h"

static int nibble(int c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

int main(void) {
    char line[256];
    while (fgets(line, sizeof line, stdin)) {
        unsigned char header[PRACTICE_ROM_HEADER_BYTES];
        int ok = strlen(line) >= 2 * PRACTICE_ROM_HEADER_BYTES;
        for (int at = 0; ok && at < PRACTICE_ROM_HEADER_BYTES; ++at) {
            int high = nibble(line[2 * at]), low = nibble(line[2 * at + 1]);
            if (high < 0 || low < 0) ok = 0;
            else header[at] = (unsigned char)(high * 16 + low);
        }
        printf("%d\n", ok ? practice_rom(header) : -1);
    }
    return 0;
}

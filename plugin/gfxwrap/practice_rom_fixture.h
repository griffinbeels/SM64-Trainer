/* Test hosts only: a cartridge header as Project64 1.6 stores it (word-swapped),
 * for the ROM a host pretends to have opened. The wrapper and renderer read it
 * through practice_rom.h exactly as they read the emulator's. */
#pragma once
#include <string.h>

static void practice_rom_fixture(unsigned char out[0x40], const char *name, char country) {
    unsigned char header[0x40];
    size_t length = strlen(name);
    memset(header, 0, sizeof header);
    header[0] = 0x80; header[1] = 0x37; header[2] = 0x12; header[3] = 0x40;
    memset(header + 0x20, ' ', 20);
    memcpy(header + 0x20, name, length < 20 ? length : 20);
    header[0x3E] = (unsigned char)country;
    for (int at = 0; at < 0x40; at += 4) {
        out[at] = header[at + 3]; out[at + 1] = header[at + 2];
        out[at + 2] = header[at + 1]; out[at + 3] = header[at];
    }
}

/* The ROM the trainer practises on, and the one a real run uses. */
#define PRACTICE_FIXTURE_USAMUNE(out) practice_rom_fixture((out), "SM64 USAMUNE v1.93u", 'E')
#define PRACTICE_FIXTURE_VANILLA(out) practice_rom_fixture((out), "SUPER MARIO 64", 'E')

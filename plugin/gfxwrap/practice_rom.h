/* THE PRACTICE ROM RULE for native code: is the cartridge Project64 opened one
 * the trainer practises on? Only then do the capture wrapper and the renderer
 * overlay do anything beyond the baseline LINK GLideN64 renderer. Vanilla
 * SM64, another SM64 ROM hack and every other game run as the plain renderer
 * (his ruling, 2026-09-16: "when we play on the vanilla rom, it should function
 * identically to the baseline graphics plugin we forked from").
 *
 * Mirrors src/sm64_events/core/onboarding.py::identify_rom exactly: "SM64
 * USAMUNE v1.93u" with country 'E' (US), or any "SM64 USAMUNE" name with
 * country 'J' (JP). tests/test_practice_rom.py runs this function and the
 * Python one over the same headers in both byte orders.
 *
 * `raw` is the first 0x40 bytes of the ROM image as the emulator stores it:
 * big-endian (80 37 12 40) or word-swapped (40 12 37 80, Project64 1.6). Pure
 * and allocation-free; the caller guards the read of the emulator's pointer. */
#pragma once
#include <string.h>

#define PRACTICE_ROM_HEADER_BYTES 0x40

static int practice_rom_normalise(const unsigned char *raw, unsigned char *out) {
    if (raw[0] == 0x80 && raw[1] == 0x37 && raw[2] == 0x12 && raw[3] == 0x40) {
        memcpy(out, raw, PRACTICE_ROM_HEADER_BYTES);
        return 1;
    }
    if (raw[0] == 0x40 && raw[1] == 0x12 && raw[2] == 0x37 && raw[3] == 0x80) {
        for (int at = 0; at < PRACTICE_ROM_HEADER_BYTES; at += 4) {
            out[at] = raw[at + 3]; out[at + 1] = raw[at + 2];
            out[at + 2] = raw[at + 1]; out[at + 3] = raw[at];
        }
        return 1;
    }
    return 0;
}

static int practice_rom(const unsigned char *raw) {
    unsigned char header[PRACTICE_ROM_HEADER_BYTES];
    char name[21];
    int length = 20;
    if (!raw || !practice_rom_normalise(raw, header)) return 0;
    memcpy(name, header + 0x20, 20);
    name[20] = '\0';
    while (length > 0 && (name[length - 1] == ' ' || name[length - 1] == '\0'))
        name[--length] = '\0';
    if (header[0x3E] == 'E') return strcmp(name, "SM64 USAMUNE v1.93u") == 0;
    if (header[0x3E] == 'J') return strncmp(name, "SM64 USAMUNE", 12) == 0;
    return 0;
}

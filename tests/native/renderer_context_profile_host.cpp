#include "renderer_context_profile.h"
#include <stdio.h>

static void set_name(unsigned char *rom, const char *name) {
    memset(rom + 0x20, ' ', 20);
    memcpy(rom + 0x20, name, strlen(name));
}

int main() {
    unsigned char rom[0x40]{};
    rom[0] = 0x80; rom[1] = 0x37; rom[2] = 0x12; rom[3] = 0x40;
    set_name(rom, "SM64 USAMUNE v1.93u"); rom[0x3e] = 'E';
    const int core = 1, compatibility = 2;
    struct Case { const char *request; int expected; };
    const Case cases[] = {{nullptr, core}, {"", core}, {"core", core},
        {"compatibility", compatibility}, {"COMPATIBILITY", core},
        {"compatibility ", core}, {"unsupported", core},
        {"compatibility-plus-an-overlong-value", core}};
    for (const auto &item : cases) {
        if (!SetEnvironmentVariableA("SM64_TRAINER_GL_PROFILE", item.request)) return 1;
        if (renderer_context_profile::select(rom, item.request, core, compatibility) != item.expected ||
            renderer_context_profile::requested(rom, core, compatibility) != item.expected) return 2;
    }
    SetEnvironmentVariableA("SM64_TRAINER_GL_PROFILE", "compatibility");
    // A missing header, vanilla game or unsupported US version keeps baseline.
    if (renderer_context_profile::requested(nullptr, core, compatibility) != core) return 3;
    set_name(rom, "SUPER MARIO 64");
    if (renderer_context_profile::requested(rom, core, compatibility) != core) return 4;
    set_name(rom, "SM64 USAMUNE v1.92u");
    if (renderer_context_profile::requested(rom, core, compatibility) != core) return 5;
    // JP, including Project64's stored word order, uses the shared ROM gate.
    set_name(rom, "SM64 USAMUNE v1.93j"); rom[0x3e] = 'J';
    if (renderer_context_profile::requested(rom, core, compatibility) != compatibility) return 6;
    unsigned char swapped[0x40]{};
    for (int at = 0; at < 0x40; at += 4)
        for (int n = 0; n < 4; ++n) swapped[at + n] = rom[at + 3 - n];
    if (renderer_context_profile::requested(swapped, core, compatibility) != compatibility) return 7;
    puts("context profile: 8 requests, 3 baseline refusals, JP + stored-word-order passed");
    return 0;
}

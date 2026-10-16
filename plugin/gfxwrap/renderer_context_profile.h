/* Opt-in A/B experiment for external capture compatibility. LINK normally
 * requests a core context. Keep that baseline for every other ROM and unless
 * the human explicitly requests the compatibility variant before game launch.
 * This changes no context version and creates no additional context/thread.
 *
 * Discord's shared-texture 0x500 error is consistent with a legacy-state/core
 * mismatch, but not proof of one. Successful capture would not itself prove
 * its cold initialization stall is gone. */
#pragma once
#include <windows.h>
#include "practice_rom.h"

namespace renderer_context_profile {
inline int select(const unsigned char *rom, const char *request, int core, int compatibility) {
    return practice_rom(rom) && request && !strcmp(request, "compatibility") ? compatibility : core;
}
inline int requested(const unsigned char *rom, int core, int compatibility) {
    if (!practice_rom(rom)) return core;
    char request[32]{};
    const DWORD length = GetEnvironmentVariableA("SM64_TRAINER_GL_PROFILE", request, sizeof request);
    if (!length || length >= sizeof request) return core;
    return select(rom, request, core, compatibility);
}
} // namespace renderer_context_profile

/* Private offline audio probe. No sound device, emulator attach or memory writes. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "libsm64.h"
#include "decomp/include/PR/ultratypes.h"
#include "decomp/include/audio_defines.h"

static double milliseconds(void) {
    LARGE_INTEGER ticks, frequency;
    QueryPerformanceCounter(&ticks);
    QueryPerformanceFrequency(&frequency);
    return 1000.0 * (double)ticks.QuadPart / (double)frequency.QuadPart;
}

int main(int argc, char **argv) {
    enum { MAX_VALUES = 544 * 2 * 2, GUARD_VALUES = 32, TICKS = 180 };
    uint8_t *rom;
    int16_t buffer[MAX_VALUES + GUARD_VALUES];
    float position[3] = {0, 0, 0}; /* Stable storage: engine retains this pointer. */
    FILE *input, *pcm;
    double begin, init_ms;
    unsigned total_frames = 0, tick;
    int cues;
    if (argc != 4) {
        fprintf(stderr, "usage: probe normalized-rom output.pcm silence|cues|half|mute\n");
        return 2;
    }
    cues = strcmp(argv[3], "cues") == 0 || strcmp(argv[3], "half") == 0 || strcmp(argv[3], "mute") == 0;
    if (!cues && strcmp(argv[3], "silence") != 0) return 2;
    input = fopen(argv[1], "rb");
    if (!input) return 3;
    rom = (uint8_t *)malloc(0x800000);
    if (!rom || fread(rom, 1, 0x800000, input) != 0x800000) return 4;
    fclose(input);
    pcm = fopen(argv[2], "wb");
    if (!pcm) return 5;
    begin = milliseconds();
    sm64_audio_init(rom);
    if (strcmp(argv[3], "half") == 0) sm64_set_sound_volume(0.5f);
    if (strcmp(argv[3], "mute") == 0) sm64_set_sound_volume(0.0f);
    init_ms = milliseconds() - begin;
    printf("{\"init_ms\":%.6f,\"ticks\":[", init_ms);
    for (tick = 0; tick < TICKS; ++tick) {
        uint32_t count, i, expected = tick % 3 == 0 ? 544 : 528;
        double cost;
        /* Canonical commands, not inferred from poses. Empty control run uses none. */
        if (cues && tick == 15) sm64_play_sound(SOUND_MARIO_YAH_WAH_HOO, position);
        if (cues && tick == 45) sm64_play_sound(SOUND_ACTION_TERRAIN_LANDING, position);
        if (cues && tick == 75) sm64_play_sound(SOUND_MARIO_YAHOO_WAHA_YIPPEE, position);
        if (cues && tick == 105) sm64_play_sound(SOUND_ACTION_METAL_LANDING, position);
        for (i = 0; i < MAX_VALUES + GUARD_VALUES; ++i) buffer[i] = (int16_t)0x5aa5;
        begin = milliseconds();
        count = sm64_audio_tick(tick % 3 == 0 ? 0 : 2000, 1100, buffer);
        cost = milliseconds() - begin;
        if (count != expected) return 6;
        /* Two blocks, each count stereo frames; everything beyond must be untouched. */
        for (i = 4 * count; i < MAX_VALUES + GUARD_VALUES; ++i)
            if (buffer[i] != (int16_t)0x5aa5) return 7;
        if (fwrite(buffer, sizeof(int16_t), 4 * count, pcm) != 4 * count) return 8;
        total_frames += 2 * count;
        printf("%s{\"frames\":%u,\"ms\":%.6f}", tick ? "," : "", 2 * count, cost);
    }
    if (fclose(pcm)) return 9;
    printf("],\"frames\":%u,\"rate\":32000,\"channels\":2,\"bits\":16}\n", total_frames);
    free(rom);
    /* The upstream loader has no complete teardown. One ROM/mode per child gives
       independent globals and bounds its leaked ROM copy to this process. */
    return 0;
}

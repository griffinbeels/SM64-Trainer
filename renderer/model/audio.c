/* Owned loader and tick around libsm64's audio engine. Replaces upstream
 * load_audio_banks, which leaked its ROM copy and never bounds-checked. */
#include "audio.h"
#include "decomp/tools/convUtils.h"
#include "decomp/audio/external.h"
#include "decomp/audio/load.h"
#include "decomp/audio/load_dat.h"
#include "load_audio_data.h"
#include "play_sound.h"
#include <stdlib.h>
#include <string.h>

extern unsigned char *gCtlSeqs;
extern u8 sNumProcessedSoundRequests;

static uint8_t *copy;
static struct seqFile *ctl, *tbl, *seq;
static f32 source[3]; /* at the listener: centred, undistanced */

static uint32_t be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static uint32_t be16(const uint8_t *p) { return (uint32_t)p[0] << 8 | p[1]; }

/* A seq file header whose every bank lies inside its own region. */
static int header(const uint8_t *rom, uint32_t at, uint32_t end, uint32_t type) {
    const uint32_t span = end - at, count = be16(rom + at + 2);
    if (be16(rom + at) != type || !count || count > 64 || 4 + 8 * count > span) return 0;
    for (uint32_t i = 0; i < count; ++i) {
        const uint32_t offset = be32(rom + at + 4 + 8 * i), length = be32(rom + at + 8 + 8 * i);
        if (offset > span || length > span - offset) return 0;
    }
    return 1;
}

static void release(void) {
    g_is_audio_initialized = false;
    if (gCtlSeqs) { ctl_free(); gCtlSeqs = NULL; }
    free(ctl); free(tbl); free(seq); free(copy);
    ctl = tbl = seq = NULL; copy = NULL;
    gSoundDataADSR = gSoundDataRaw = gMusicData = NULL; gBankSetsData = NULL;
}

int32_t sa_open(const uint8_t *rom, size_t bytes) {
    if (copy) return SA_BUSY;
    if (!rom || bytes != SA_ROM_BYTES) return SA_ARGUMENT;
    if (!header(rom, SA_CTL_OFFSET, SA_TBL_OFFSET, 1) || !header(rom, SA_TBL_OFFSET, SA_SEQ_OFFSET, 2)
            || !header(rom, SA_SEQ_OFFSET, SA_BANKS_OFFSET, 3)) return SA_INVALID_ROM;
    copy = malloc(bytes);
    if (!copy) return SA_MEMORY;
    memcpy(copy, rom, bytes);
    gSoundDataADSR = ctl = parse_seqfile(copy + SA_CTL_OFFSET);
    gSoundDataRaw = tbl = parse_seqfile(copy + SA_TBL_OFFSET);
    gMusicData = seq = parse_seqfile(copy + SA_SEQ_OFFSET);
    if (!ctl || !tbl || !seq || !gCtlSeqs) { release(); return SA_MEMORY; }
    /* Upstream's bank-set fixup, inside the region it was checked against. */
    gBankSetsData = copy + SA_BANKS_OFFSET;
    memmove(gBankSetsData + 0x45, gBankSetsData + 0x44, 0x5B);
    gBankSetsData[0x45] = 0x00;
    ptrs_to_offsets(ctl);
    audio_init();
    sound_init();
    sound_reset(0);
    gAudioVolume = 1.0f; /* loudness is the caller's exact output scale */
    g_is_audio_initialized = true;
    return SA_OK;
}

int32_t sa_play(uint32_t sound_bits) {
    if (!copy) return SA_CLOSED;
    play_sound(sound_bits, source);
    return SA_OK;
}

int32_t sa_stop_all(void) {
    if (!copy) return SA_CLOSED;
    sNumProcessedSoundRequests = sSoundRequestCount;
    stop_sounds_from_source(source);
    return SA_OK;
}

int32_t sa_tick(uint32_t queued_frames, int16_t *out, uint32_t capacity_frames, uint32_t *frames) {
    if (frames) *frames = 0;
    if (!copy) return SA_CLOSED;
    const uint32_t block = queued_frames < SA_TICK_TARGET_FRAMES ? 544u : 528u;
    if (!out || !frames || capacity_frames < 2 * block) return SA_ARGUMENT;
    update_game_sound();
    for (uint32_t i = 0; i < 2; ++i) create_next_audio_buffer(out + i * 2 * block, block);
    *frames = 2 * block;
    return SA_OK;
}

int32_t sa_close(void) {
    if (!copy) return SA_CLOSED;
    release();
    return SA_OK;
}

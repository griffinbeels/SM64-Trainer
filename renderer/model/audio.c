/* Owned loader and tick around libsm64's audio engine. Replaces upstream
 * load_audio_banks, which leaked its ROM copy and never bounds-checked. */
#include "audio.h"
#include "decomp/tools/convUtils.h"
#include "decomp/audio/external.h"
#include "decomp/audio/load.h"
#include "decomp/audio/load_dat.h"
#include "load_audio_data.h"
#include "play_sound.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

extern unsigned char *gCtlSeqs;
extern u8 sNumProcessedSoundRequests;
extern u8 sNumSoundsPerBank[];

static uint8_t *copy;
/* The bank-set table as upstream reads it on a little-endian host: u16
 * offsets in host order, then the bank lists, in the 0x100 bytes audio_init
 * copies. Upstream's loader instead pointed one byte into the ROM's
 * big-endian table and slid its lists along by one: the same values, but only
 * for US's exact table. */
static uint8_t bank_sets[0x100];
static struct seqFile *ctl, *tbl, *seq;
/* One source per ghost, camera-relative like SM64's own cameraToObject, so
 * the engine pans, distances and prioritises each ghost as the game would
 * Mario at that spot. gains[] multiplies get_sound_volume (patches.py). */
static f32 sources[SA_SOURCES][3];
static f32 gains[SA_SOURCES];
#define SA_PLACE_BOUND 100000.0f /* silent beyond 22000; keeps distance a valid u32 */

static uint32_t be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static uint32_t be16(const uint8_t *p) { return (uint32_t)p[0] << 8 | p[1]; }

/* A seq file header whose every bank lies inside its own region (offsets in
 * the span, which begins at CTL). */
static int header(const uint8_t *audio, uint32_t at, uint32_t end, uint32_t type) {
    const uint8_t *seq = audio + at;
    const uint32_t span = end - at, count = be16(seq + 2);
    if (be16(seq) != type || !count || count > 64 || 4 + 8 * count > span) return 0;
    for (uint32_t i = 0; i < count; ++i) {
        const uint32_t offset = be32(seq + 4 + 8 * i), length = be32(seq + 8 + 8 * i);
        if (offset > span || length > span - offset) return 0;
    }
    return 1;
}

/* The big-endian table into bank_sets: every offset past the offsets and
 * every list inside the table, or nothing. */
static int load_bank_sets(const uint8_t *table) {
    const uint32_t first = be16(table), count = first / 2;
    uint32_t i;
    if (!count || first % 2 || first >= SA_BANKS_BYTES) return 0;
    for (i = 0; i < count; ++i) {
        const uint32_t at = be16(table + 2 * i);
        if (at < first || at >= SA_BANKS_BYTES || 1u + table[at] > SA_BANKS_BYTES - at) return 0;
    }
    memset(bank_sets, 0, sizeof(bank_sets));
    for (i = 0; i < count; ++i) {
        bank_sets[2 * i] = table[2 * i + 1];
        bank_sets[2 * i + 1] = table[2 * i];
    }
    memcpy(bank_sets + first, table + first, SA_BANKS_BYTES - first);
    return 1;
}

static void release(void) {
    g_is_audio_initialized = false;
    if (gCtlSeqs) { ctl_free(); gCtlSeqs = NULL; }
    free(ctl); free(tbl); free(seq); free(copy);
    ctl = tbl = seq = NULL; copy = NULL;
    gSoundDataADSR = gSoundDataRaw = gMusicData = NULL; gBankSetsData = NULL;
}

int32_t sa_open(const uint8_t *audio, size_t bytes, const sa_regions *regions) {
    uint32_t tbl_at, seq_at, banks_at;
    if (copy) return SA_BUSY;
    if (!audio || !regions || regions->tbl <= regions->ctl || regions->seq <= regions->tbl
            || regions->banks <= regions->seq || bytes != SA_SPAN(*regions)) return SA_ARGUMENT;
    tbl_at = regions->tbl - regions->ctl; seq_at = regions->seq - regions->ctl;
    banks_at = regions->banks - regions->ctl;
    if (!header(audio, 0, tbl_at, 1) || !header(audio, tbl_at, seq_at, 2)
            || !header(audio, seq_at, banks_at, 3) || !load_bank_sets(audio + banks_at)) return SA_INVALID_ROM;
    /* Upstream copied the whole 8 MB ROM; every parse is relative to its own
     * region, so the span alone is enough. */
    copy = malloc(bytes);
    if (!copy) return SA_MEMORY;
    memcpy(copy, audio, bytes);
    gSoundDataADSR = ctl = parse_seqfile(copy);
    gSoundDataRaw = tbl = parse_seqfile(copy + tbl_at);
    gMusicData = seq = parse_seqfile(copy + seq_at);
    if (!ctl || !tbl || !seq || !gCtlSeqs) { release(); return SA_MEMORY; }
    gBankSetsData = bank_sets;
    /* The engine's own bound on a request's sound id (process_sound_request),
     * as the build whose data is loaded has it: past it lies no sound. */
    sNumSoundsPerBank[2] = sNumSoundsPerBank[7] = regions->voice_menu_sounds;
    ptrs_to_offsets(ctl);
    audio_init();
    sound_init();
    sound_reset(0);
    gAudioVolume = 1.0f; /* loudness is the caller's exact output scale */
    memset(sources, 0, sizeof(sources));
    for (uint32_t i = 0; i < SA_SOURCES; ++i) gains[i] = 1.0f;
    g_is_audio_initialized = true;
    return SA_OK;
}

int32_t sa_place(uint32_t source, const float position[3], float gain) {
    if (!copy) return SA_CLOSED;
    if (source >= SA_SOURCES || !position || !(gain >= 0.0f && gain <= 1.0f)) return SA_ARGUMENT;
    for (uint32_t i = 0; i < 3; ++i) if (!isfinite(position[i])) return SA_ARGUMENT;
    for (uint32_t i = 0; i < 3; ++i)
        sources[source][i] = position[i] > SA_PLACE_BOUND ? SA_PLACE_BOUND
                           : position[i] < -SA_PLACE_BOUND ? -SA_PLACE_BOUND : position[i];
    gains[source] = gain;
    return SA_OK;
}

int32_t sa_play(uint32_t source, uint32_t sound_bits) {
    if (!copy) return SA_CLOSED;
    if (source >= SA_SOURCES) return SA_ARGUMENT;
    play_sound(sound_bits, sources[source]);
    return SA_OK;
}

float sa_source_gain(const float *x) {
    for (uint32_t i = 0; i < SA_SOURCES; ++i) if (x == sources[i]) return gains[i];
    return 1.0f;
}

int32_t sa_stop_all(void) {
    if (!copy) return SA_CLOSED;
    sNumProcessedSoundRequests = sSoundRequestCount;
    for (uint32_t i = 0; i < SA_SOURCES; ++i) stop_sounds_from_source(sources[i]);
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

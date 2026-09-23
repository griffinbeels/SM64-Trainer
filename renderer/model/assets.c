#include "internal.h"
#include "load_tex_data.h"
#include <stdlib.h>
#include <string.h>

#define ANIM_BASE 0x4ec000u
#define TEXTURE_BASE 1132368u
#define TEXTURE_SPAN (128u * 1024u)
/* common1 (segment 3): Mario's lava flame among the global particle models.
 * Located by its decoded flame lists in the US and Usamune US ROMs. */
#define COMMON1_BASE 0x201410u
#define COMMON1_SPAN (256u * 1024u)
/* common0 (segment 8): the Bob-omb. Located by its decoded lists in the US
 * ROM (bobomb_seg8_dl_08022D08 at 0x22D08). */
#define COMMON0_BASE 0x1F2200u
#define COMMON0_SPAN (256u * 1024u)
static const struct { unsigned cell; uint32_t offset; } held_textures[] = {
    {SG_ATLAS_CELL_BOBOMB_LEFT, 0x1DA60u}, {SG_ATLAS_CELL_BOBOMB_RIGHT, 0x1EA60u},
    {SG_ATLAS_CELL_BOBOMB_EYES, 0x21A60u}, {SG_ATLAS_CELL_BOBOMB_BLINK, 0x22260u},
};

static uint16_t be16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }
static uint32_t be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}
static int range(size_t at, size_t count, size_t bytes)
{ return at <= bytes && count <= bytes - at; }

/* The model needs only bounded MIO0 decoding and RGBA16 conversion. No image
 * encoder, filesystem utility, directory enumerator or audio loader is linked.
 * The caller has checked that span bytes from base lie inside the ROM.
 */
static int mio0(const uint8_t *rom, uint32_t base, uint32_t span, uint8_t **out, uint32_t *out_size)
{
    const uint8_t *src = rom + base;
    uint32_t size, compressed, raw, flags = 16, written = 0;
    unsigned bit = 0;
    uint8_t *decoded;
    *out = NULL; *out_size = 0;
    if (memcmp(src, "MIO0", 4)) return SG_INVALID_ROM;
    size = be32(src + 4); compressed = be32(src + 8); raw = be32(src + 12);
    if (!size || size > 1024u * 1024u || compressed < 16 ||
        raw < compressed || raw > span)
        return SG_INVALID_ROM;
    decoded = malloc(size);
    if (!decoded) return SG_OUT_OF_MEMORY;
    while (written < size) {
        if (flags >= be32(src + 8)) goto bad;
        if (src[flags] & (0x80u >> bit)) {
            if (raw >= span) goto bad;
            decoded[written++] = src[raw++];
        } else {
            uint32_t length, distance, j;
            if (!range(compressed, 2, be32(src + 12))) goto bad;
            length = (src[compressed] >> 4) + 3u;
            distance = (((uint32_t)src[compressed] & 15u) << 8) + src[compressed + 1] + 1u;
            compressed += 2;
            if (distance > written || length > size - written) goto bad;
            for (j = 0; j < length; ++j) {
                decoded[written] = decoded[written - distance];
                ++written;
            }
        }
        if (++bit == 8) { bit = 0; ++flags; }
    }
    *out = decoded; *out_size = size;
    return SG_OK;
bad:
    free(decoded);
    return SG_INVALID_ROM;
}

/* One RGBA16 texture into its 64-pixel atlas cell; its size is the cell's. */
static int blit(struct sg_model *model, unsigned tile, const uint8_t *decoded, uint32_t size, size_t offset)
{
    unsigned x, y, width = mario_tex_widths[tile], height = mario_tex_heights[tile];
    if (width > 64 || height > 64 || !range(offset, width * height * 2u, size)) return SG_INVALID_ROM;
    for (y = 0; y < height; ++y) for (x = 0; x < width; ++x) {
        unsigned color = be16(decoded + offset + (y * width + x) * 2u);
        uint8_t *dest = model->atlas + ((y * SG_MODEL_ATLAS_WIDTH) + tile * 64u + x) * 4u;
        dest[0] = (uint8_t)(((color >> 11) & 31u) * 255u / 31u);
        dest[1] = (uint8_t)(((color >> 6) & 31u) * 255u / 31u);
        dest[2] = (uint8_t)(((color >> 1) & 31u) * 255u / 31u);
        dest[3] = (color & 1u) ? 255 : 0;
    }
    return SG_OK;
}

/* Held objects' textures; failure leaves their cells empty and marks them
 * unavailable rather than refusing Mario. */
static void held_cells(struct sg_model *model, const uint8_t *rom)
{
    uint8_t *decoded;
    uint32_t size;
    unsigned i;
    model->held_available = 0;
    if (mio0(rom, COMMON0_BASE, COMMON0_SPAN, &decoded, &size) != SG_OK) return;
    for (i = 0; i < sizeof(held_textures) / sizeof(held_textures[0]); ++i)
        if (blit(model, held_textures[i].cell, decoded, size, held_textures[i].offset) != SG_OK) {
            free(decoded); return;
        }
    free(decoded);
    model->held_available = 1u << SG_HELD_BOBOMB;
}

static int textures(struct sg_model *model, const uint8_t *rom)
{
    uint32_t size, common1_size;
    unsigned tile;
    uint8_t *decoded, *common1;
    int status = mio0(rom, TEXTURE_BASE, TEXTURE_SPAN, &decoded, &size);
    if (status != SG_OK) return status;
    for (tile = 0; tile < SG_MARIO_ATLAS_CELLS; ++tile)
        if (blit(model, tile, decoded, size, (size_t)mario_tex_offsets[tile]) != SG_OK) goto bad;
    held_cells(model, rom);
    /* Same segment: Mario's particle models. Unsupported lists leave none;
     * the flame (common1) is added only when its own lists read too. */
    if (sg_particles_load(&model->particles, decoded, size) == SG_OK &&
        mio0(rom, COMMON1_BASE, COMMON1_SPAN, &common1, &common1_size) == SG_OK) {
        sg_particles_add_common1(&model->particles, common1, common1_size);
        free(common1);
    }
    free(decoded);
    return SG_OK;
bad:
    free(decoded);
    return SG_INVALID_ROM;
}

static int animations(struct sg_model *model, const uint8_t *rom, size_t bytes)
{
    uint32_t i;
    size_t total = 0;
    if (be32(rom + ANIM_BASE) != SG_ANIMATION_COUNT) return SG_INVALID_ROM;
    for (i = 0; i < SG_ANIMATION_COUNT; ++i) {
        size_t offset = be32(rom + ANIM_BASE + 8 + i * 8);
        size_t length = be32(rom + ANIM_BASE + 12 + i * 8);
        size_t values, indices, end, value_count, j;
        const uint8_t *src;
        struct Animation *anim = &model->animations[i];
        if (offset < 8 + SG_ANIMATION_COUNT * 8 || length < 24 || length > 256u * 1024u ||
            total > 4u * 1024u * 1024u - length ||
            !range(offset, length, bytes - ANIM_BASE)) return SG_INVALID_ROM;
        total += length;
        src = rom + ANIM_BASE + offset;
        values = be32(src + 12); indices = be32(src + 16); end = be32(src + 20);
        /* Mario's fixed skeleton has 20 animated parts + root translation:
         * 63 (count,offset) pairs. Every supported US animation uses 126 u16s.
         */
        if (be16(src + 10) != 20 || indices < 24 || values < indices ||
            values - indices != 252 || end < values || end > length ||
            ((values | indices | end) & 1u) || !be16(src + 8) || be16(src + 8) > INT16_MAX)
            return SG_INVALID_ROM;
        value_count = (end - values) / 2;
        if (!value_count) return SG_INVALID_ROM;
        for (j = 0; j < 126; j += 2) {
            size_t count = be16(src + indices + j * 2), start = be16(src + indices + (j + 1) * 2);
            if (!count || !range(start, count, value_count)) return SG_INVALID_ROM;
        }
        anim->index = malloc(252);
        anim->values = malloc(value_count * sizeof(s16));
        if (!anim->index || !anim->values) return SG_OUT_OF_MEMORY;
        anim->flags = (s16)be16(src);
        anim->animYTransDivisor = (s16)be16(src + 2);
        anim->startFrame = (s16)be16(src + 4);
        anim->loopStart = (s16)be16(src + 6);
        anim->loopEnd = (s16)be16(src + 8);
        anim->unusedBoneCount = 20;
        anim->length = (u32)length;
        for (j = 0; j < 126; ++j) anim->index[j] = be16(src + indices + j * 2);
        for (j = 0; j < value_count; ++j) anim->values[j] = (s16)be16(src + values + j * 2);
    }
    return SG_OK;
}

int sg_assets_load(struct sg_model *model, const uint8_t *rom, size_t bytes)
{
    int status;
    if (!rom || bytes != 8u * 1024u * 1024u || be32(rom) != 0x80371240u ||
        !range(ANIM_BASE, 8 + SG_ANIMATION_COUNT * 8, bytes) ||
        !range(TEXTURE_BASE, TEXTURE_SPAN, bytes) ||
        !range(COMMON1_BASE, COMMON1_SPAN, bytes)) return SG_INVALID_ROM;
    status = textures(model, rom);
    if (status != SG_OK) return status;
    return animations(model, rom, bytes);
}

void sg_assets_free(struct sg_model *model)
{
    unsigned i;
    for (i = 0; i < SG_ANIMATION_COUNT; ++i) {
        free(model->animations[i].index);
        free(model->animations[i].values);
        model->animations[i].index = NULL;
        model->animations[i].values = NULL;
    }
}

#ifdef SG_MODEL_TESTING
static volatile LONG fail_validation_allocation;
void sg_model_test_validate_allocation_failure(int enabled)
{ InterlockedExchange(&fail_validation_allocation, enabled); }
#endif

int32_t sg_model_validate_rom(const uint8_t *rom, size_t bytes)
{
    struct sg_model *temporary;
    int status;
#ifdef SG_MODEL_TESTING
    if (InterlockedCompareExchange(&fail_validation_allocation, 0, 0)) return SG_OUT_OF_MEMORY;
#endif
    temporary = calloc(1, sizeof(*temporary));
    if (!temporary) return SG_OUT_OF_MEMORY;
    status = sg_assets_load(temporary, rom, bytes);
    sg_assets_free(temporary);
    free(temporary);
    return status;
}

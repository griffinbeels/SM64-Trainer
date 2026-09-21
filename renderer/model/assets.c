#include "internal.h"
#include "load_tex_data.h"
#include <stdlib.h>
#include <string.h>

#define ANIM_BASE 0x4ec000u
#define TEXTURE_BASE 1132368u
#define TEXTURE_SPAN (128u * 1024u)

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
 */
static int textures(struct sg_model *model, const uint8_t *rom)
{
    const uint8_t *src = rom + TEXTURE_BASE;
    uint32_t size, compressed, raw, flags = 16, written = 0;
    unsigned bit = 0, tile;
    uint8_t *decoded;
    if (memcmp(src, "MIO0", 4)) return SG_INVALID_ROM;
    size = be32(src + 4); compressed = be32(src + 8); raw = be32(src + 12);
    if (!size || size > 1024u * 1024u || compressed < 16 ||
        raw < compressed || raw > TEXTURE_SPAN)
        return SG_INVALID_ROM;
    decoded = malloc(size);
    if (!decoded) return SG_OUT_OF_MEMORY;
    while (written < size) {
        if (flags >= be32(src + 8)) goto bad;
        if (src[flags] & (0x80u >> bit)) {
            if (raw >= TEXTURE_SPAN) goto bad;
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
    for (tile = 0; tile < NUM_USED_TEXTURES; ++tile) {
        unsigned x, y, width = mario_tex_widths[tile], height = mario_tex_heights[tile];
        size_t offset = (size_t)mario_tex_offsets[tile];
        if (width > 64 || height > 64 || !range(offset, width * height * 2u, size)) goto bad;
        for (y = 0; y < height; ++y) for (x = 0; x < width; ++x) {
            unsigned color = be16(decoded + offset + (y * width + x) * 2u);
            uint8_t *dest = model->atlas + ((y * SG_MODEL_ATLAS_WIDTH) + tile * 64u + x) * 4u;
            dest[0] = (uint8_t)(((color >> 11) & 31u) * 255u / 31u);
            dest[1] = (uint8_t)(((color >> 6) & 31u) * 255u / 31u);
            dest[2] = (uint8_t)(((color >> 1) & 31u) * 255u / 31u);
            dest[3] = (color & 1u) ? 255 : 0;
        }
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
        !range(TEXTURE_BASE, TEXTURE_SPAN, bytes)) return SG_INVALID_ROM;
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

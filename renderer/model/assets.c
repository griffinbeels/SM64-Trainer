#include "internal.h"
#include "load_tex_data.h"
#include <stdlib.h>
#include <string.h>

/* ROM blocks are named by their US offset, here and in HELD_MODELS
 * (tools/build_model.py); cartridge_block maps one to the running cartridge. */
#define ANIM_BASE 0x4ec000u
#define TEXTURE_BASE 1132368u
#define TEXTURE_SPAN (128u * 1024u)
/* common1 (segment 3): Mario's lava flame among the global particle models.
 * Located by its decoded flame lists in the US and Usamune US ROMs. */
#define COMMON1_BASE 0x201410u
#define COMMON1_SPAN (256u * 1024u)
/* Held objects' textures, each from its model's own MIO0 segment
 * (HELD_MODELS in tools/build_model.py generates the table). */
#define HELD_SEGMENT_SPAN (256u * 1024u)
/* Where Usamune JP (country 'J', v1.93j) keeps each block. Every one decodes
 * to the same bytes as its US block (Mario's animation table is the same
 * 0x8DC16 raw bytes); each JP offset is where that decoded content was found
 * by searching SM64_USAMUNE_v1.93j.z64, 2026-10-16. Group 6 is the one
 * relocation inside a block: US inserted 0xF0 bytes ahead of Mips's texture,
 * which JP keeps at its decomp address (the Mips shift in HELD_MODELS), so a
 * JP texture offset in that block is 0xF0 lower. The model itself is the same
 * graph and lists on both cartridges. */
static const struct { uint32_t us, jp; int32_t jp_shift; } jp_blocks[] = {
    {ANIM_BASE, 0x4e9fa0u, 0},      /* Mario's animation table */
    {TEXTURE_BASE, 0x112b50u, 0},   /* group0 (segment 4): Mario, his particles */
    {COMMON1_BASE, 0x1ff740u, 0},   /* common1 (segment 3) */
    {0x1f2200u, 0x1f0530u, 0},      /* common0 (segment 8): Bob-omb, Chuckya, boxes, shell */
    {0x160670u, 0x15ea70u, 0},      /* penguin's group */
    {0x134d20u, 0x133120u, 0},      /* King Bob-omb's group */
    {0x1d8310u, 0x1d6660u, -0xf0},  /* group 6: Mips */
    {0x12a7e0u, 0x128be0u, 0},      /* Hoot's group */
    {0x1521d0u, 0x1505d0u, 0},      /* Ukiki's group */
};
static const struct { unsigned kind, cell; uint32_t segment, offset; } held_textures[] = {
    SG_HELD_TEXTURES
};
typedef char sg_held_cells_fill_atlas[(SG_MARIO_ATLAS_CELLS + SG_HELD_CELL_COUNT == SG_ATLAS_CELLS) ? 1 : -1];
typedef char sg_held_kinds_match[(SG_HELD_KIND_COUNT == SG_HELD_KINDS) ? 1 : -1];
SG_HELD_KIND_CHECKS

static uint16_t be16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }
static uint32_t be32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}
static int range(size_t at, size_t count, size_t bytes)
{ return at <= bytes && count <= bytes - at; }

/* The running cartridge's offset of a block named by its US offset, with the
 * shift its texture offsets take there; 0 for an unknown cartridge or block.
 * The country byte is only the map's key: the bounded parsers still judge
 * every byte at the offset they are given. */
static int cartridge_block(const uint8_t *rom, size_t bytes, uint32_t us, uint32_t span,
                           uint32_t *at, int32_t *shift)
{
    unsigned i;
    *at = 0; *shift = 0;
    if (rom[0x3e] == 'E') *at = us;
    else if (rom[0x3e] == 'J') {
        for (i = 0; i < sizeof(jp_blocks) / sizeof(jp_blocks[0]); ++i)
            if (jp_blocks[i].us == us) { *at = jp_blocks[i].jp; *shift = jp_blocks[i].jp_shift; }
    }
    return *at && range(*at, span, bytes);
}

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

/* Held objects' textures; a kind whose segment or texture fails leaves its
 * cells empty and is marked unavailable rather than refusing Mario. */
static void held_cells(struct sg_model *model, const uint8_t *rom, size_t bytes)
{
    uint8_t *decoded = NULL;
    uint32_t size = 0, segment = 0, at;
    int32_t shift = 0;
    unsigned i, present = 0, failed = 0;
    for (i = 0; i < sizeof(held_textures) / sizeof(held_textures[0]); ++i) {
        present |= 1u << held_textures[i].kind;
        if (!decoded || segment != held_textures[i].segment) {
            free(decoded);
            decoded = NULL;
            segment = held_textures[i].segment;
            if (!cartridge_block(rom, bytes, segment, HELD_SEGMENT_SPAN, &at, &shift)
                    || mio0(rom, at, HELD_SEGMENT_SPAN, &decoded, &size) != SG_OK) decoded = NULL;
        }
        if (!decoded || (int64_t)held_textures[i].offset + shift < 0
                || blit(model, held_textures[i].cell, decoded, size,
                        (size_t)((int64_t)held_textures[i].offset + shift)) != SG_OK)
            failed |= 1u << held_textures[i].kind;
    }
    free(decoded);
    model->held_available = present & ~failed;
}

/* Mario's lights, as his lists load them: gsSPLight(&l, 1) then
 * gsSPLight(&a, 2), i.e. G_MOVEMEM L0 (03860010, the directional light)
 * followed by L1 (03880010, its ambient), both in this segment (87 pairs in
 * the US ROM). The adjacent pair, and each Light's colour repeated in its copy, keep
 * texture bytes from reading as a light. A diffuse colour loaded with two
 * different ambients or directions leaves no lights: the mesh's colour
 * would not say which one lit it. */
static void body_lights(struct sg_model *model, const uint8_t *segment, uint32_t size)
{
    sg_body_lights found;
    uint32_t at, i;
    memset(&found, 0, sizeof(found));
    for (at = 0; at + 16u <= size; at += 8u) {
        uint32_t light, ambient;
        const uint8_t *l, *a;
        sg_body_light entry;
        if (be32(segment + at) != 0x03860010u || be32(segment + at + 8) != 0x03880010u) continue;
        light = be32(segment + at + 4); ambient = be32(segment + at + 12);
        if ((light >> 24) != 0x04u || (ambient >> 24) != 0x04u) goto refuse;
        light &= 0xFFFFFFu; ambient &= 0xFFFFFFu;
        if (!range(light, 16u, size) || !range(ambient, 8u, size)) goto refuse;
        l = segment + light; a = segment + ambient;
        if (memcmp(l, l + 4, 3) || memcmp(a, a + 4, 3)) goto refuse;
        memcpy(entry.diffuse, l, 3); memcpy(entry.ambient, a, 3);
        memcpy(entry.direction, l + 8, 3);
        if (!entry.direction[0] && !entry.direction[1] && !entry.direction[2]) goto refuse;
        for (i = 0; i < found.count; ++i)
            if (!memcmp(found.lights[i].diffuse, entry.diffuse, 3)) break;
        if (i < found.count) {
            if (memcmp(&found.lights[i], &entry, sizeof(entry))) goto refuse;
            continue;
        }
        if (found.count == SG_BODY_LIGHTS) goto refuse;
        found.lights[found.count++] = entry;
    }
    model->body_lights = found;
    return;
refuse:
    memset(&model->body_lights, 0, sizeof(model->body_lights));
}

static int textures(struct sg_model *model, const uint8_t *rom, size_t bytes)
{
    uint32_t size, common1_size, at, common1_at;
    int32_t shift;
    unsigned tile;
    uint8_t *decoded, *common1;
    int status;
    if (!cartridge_block(rom, bytes, TEXTURE_BASE, TEXTURE_SPAN, &at, &shift)) return SG_INVALID_ROM;
    status = mio0(rom, at, TEXTURE_SPAN, &decoded, &size);
    if (status != SG_OK) return status;
    for (tile = 0; tile < SG_MARIO_ATLAS_CELLS; ++tile)
        if (blit(model, tile, decoded, size, (size_t)mario_tex_offsets[tile]) != SG_OK) goto bad;
    held_cells(model, rom, bytes);
    body_lights(model, decoded, size);
    /* Same segment: Mario's particle models. Unsupported lists leave none;
     * the flame (common1) is added only when its own lists read too. */
    if (sg_particles_load(&model->particles, decoded, size) == SG_OK &&
        cartridge_block(rom, bytes, COMMON1_BASE, COMMON1_SPAN, &common1_at, &shift) &&
        mio0(rom, common1_at, COMMON1_SPAN, &common1, &common1_size) == SG_OK) {
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
    uint32_t i, base;
    int32_t shift;
    size_t total = 0;
    if (!cartridge_block(rom, bytes, ANIM_BASE, 8 + SG_ANIMATION_COUNT * 8, &base, &shift) ||
        be32(rom + base) != SG_ANIMATION_COUNT) return SG_INVALID_ROM;
    for (i = 0; i < SG_ANIMATION_COUNT; ++i) {
        size_t offset = be32(rom + base + 8 + i * 8);
        size_t length = be32(rom + base + 12 + i * 8);
        size_t values, indices, end, value_count, j;
        const uint8_t *src;
        struct Animation *anim = &model->animations[i];
        if (offset < 8 + SG_ANIMATION_COUNT * 8 || length < 24 || length > 256u * 1024u ||
            total > 4u * 1024u * 1024u - length ||
            !range(offset, length, bytes - base)) return SG_INVALID_ROM;
        total += length;
        src = rom + base + offset;
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
    if (!rom || bytes != 8u * 1024u * 1024u || be32(rom) != 0x80371240u) return SG_INVALID_ROM;
    status = textures(model, rom, bytes);
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

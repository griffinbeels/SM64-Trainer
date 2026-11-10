/* Fills the model's data rows (rom_data.h) from the player's ROM at open.
 * Each row names a block (a MIO0 segment or a raw geo segment, by its US
 * offset; assets.c maps it to the running cartridge) and a segmented
 * address in it. Display lists and geo layouts are read command by command
 * against their templates: every opcode must be the template's, and every
 * pointer the ROM holds must be the address of the row the template points
 * to, so the filled graph is the compiled graph with the ROM's numbers.
 * The decoded blocks live only for the fill. */
#include "internal.h"
#include "rom_data.h"
#include "rom_combiners.h"
#include "gfx_macros.h"
#include <stdlib.h>
#include <string.h>
#ifdef SG_ROM_TRACE /* /DSG_ROM_TRACE names each row that does not read */
#include <stdio.h>
#endif

#define SG_ROM_BLOCKS 16

struct block { uint32_t us; const uint8_t *data; uint8_t *owned; uint32_t size; int32_t shift; int failed; };
struct fill {
    const uint8_t *rom;
    size_t bytes;
    int write;
    struct block blocks[SG_ROM_BLOCKS];
    unsigned block_count;
};

static uint16_t be16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }
static uint32_t be32(const uint8_t *p)
{ return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }

static const struct block *block(struct fill *f, uint32_t us)
{
    unsigned i;
    struct block *b;
    for (i = 0; i < f->block_count; ++i)
        if (f->blocks[i].us == us) return f->blocks[i].failed ? NULL : &f->blocks[i];
    if (f->block_count == SG_ROM_BLOCKS) return NULL;
    b = &f->blocks[f->block_count++];
    memset(b, 0, sizeof(*b));
    b->us = us;
    if (sg_rom_block(f->rom, f->bytes, us, &b->data, &b->owned, &b->size, &b->shift) != SG_OK) {
        b->failed = 1;
        return NULL;
    }
    return b;
}

static size_t object_bytes(const sg_rom_object *row)
{
    switch (row->kind) {
    case SG_ROM_VTX: return row->count * sizeof(Vtx);
    case SG_ROM_LIGHTS: return sizeof(Lights1);
    case SG_ROM_S16: case SG_ROM_U16: return row->count * 2u;
    case SG_ROM_ANIM: return sizeof(struct Animation);
    case SG_ROM_GFX: return row->count * sizeof(Gfx);
    case SG_ROM_GEO: return row->count * sizeof(uintptr_t);
    default: return 0;
    }
}

/* The address the running cartridge gives a row: its block's shift there. */
static int address(struct fill *f, const sg_rom_object *row, uint32_t *out)
{
    const struct block *b = block(f, row->block);
    if (!b) return 0;
    *out = row->address + (uint32_t)b->shift;
    return 1;
}

/* The row whose filled storage holds pointer, among the owner's rows. */
static const sg_rom_object *owner_row(unsigned owner, uintptr_t pointer)
{
    unsigned t, i;
    for (t = 0; t < sg_rom_table_count; ++t)
        for (i = 0; i < *sg_rom_tables[t].count; ++i) {
            const sg_rom_object *row = &sg_rom_tables[t].rows[i];
            uintptr_t start = (uintptr_t)row->out;
            if (row->owner == owner && row->out && pointer >= start && pointer < start + object_bytes(row))
                return row;
        }
    return NULL;
}

/* A compiled pointer and the ROM's word for it name the same thing. Byte
 * offsets inside a vertex array count 16-byte ROM vertices. */
static int same_pointer(struct fill *f, unsigned owner, uintptr_t pointer, uint32_t word, int function_allowed)
{
    const sg_rom_object *row;
    uint32_t at, offset;
    if (!pointer) return word == 0;
    row = owner_row(owner, pointer);
    if (!row) return function_allowed && word >= 0x80000000u && word < 0x80800000u;
    offset = (uint32_t)(pointer - (uintptr_t)row->out);
    if (row->kind == SG_ROM_VTX) {
        if (offset % sizeof(Vtx)) return 0;
        offset = offset / (uint32_t)sizeof(Vtx) * 16u;
    } else if (row->kind == SG_ROM_GFX || row->kind == SG_ROM_GEO || row->kind == SG_ROM_ANIM) {
        if (offset) return 0;
    }
    return address(f, row, &at) && word == at + offset;
}

/* The row of the given kind the ROM's segmented address names. */
static const sg_rom_object *addressed_row(struct fill *f, unsigned owner, unsigned kind, uint32_t word)
{
    unsigned t, i;
    uint32_t at;
    for (t = 0; t < sg_rom_table_count; ++t)
        for (i = 0; i < *sg_rom_tables[t].count; ++i) {
            const sg_rom_object *row = &sg_rom_tables[t].rows[i];
            if (row->owner == owner && row->kind == kind && address(f, row, &at) && at == word) return row;
        }
    return NULL;
}

/* The ROM bytes of a row, need of them inside its block. */
static const uint8_t *source(struct fill *f, const sg_rom_object *row, size_t need, size_t *available)
{
    const struct block *b = block(f, row->block);
    uint32_t offset;
    if (!b) return NULL;
    offset = (row->address & 0xFFFFFFu) + (uint32_t)b->shift;
    if (offset > b->size || need > b->size - offset) return NULL;
    if (available) *available = b->size - offset;
    return b->data + offset;
}

static int fill_vtx(struct fill *f, const sg_rom_object *row)
{
    const uint8_t *src = source(f, row, (size_t)row->count * 16u, NULL);
    Vtx *out = (Vtx *)row->out;
    uint32_t i;
    if (!src) return 0;
    if (!f->write) return 1;
    for (i = 0; i < row->count; ++i, src += 16) {
        out[i].v.ob[0] = (float)(int16_t)be16(src);
        out[i].v.ob[1] = (float)(int16_t)be16(src + 2);
        out[i].v.ob[2] = (float)(int16_t)be16(src + 4);
        out[i].v.flag = be16(src + 6);
        out[i].v.tc[0] = (short)be16(src + 8);
        out[i].v.tc[1] = (short)be16(src + 10);
        memcpy(out[i].v.cn, src + 12, 4);
    }
    return 1;
}

static int fill_halves(struct fill *f, const sg_rom_object *row)
{
    const uint8_t *src = source(f, row, (size_t)row->count * 2u, NULL);
    uint16_t *out = (uint16_t *)row->out;
    uint32_t i;
    if (!src) return 0;
    if (f->write) for (i = 0; i < row->count; ++i) out[i] = be16(src + i * 2u);
    return 1;
}

static int fill_lights(struct fill *f, const sg_rom_object *row)
{
    const uint8_t *src = source(f, row, sizeof(Lights1), NULL);
    if (!src) return 0;
    if (f->write) memcpy(row->out, src, sizeof(Lights1));
    return 1;
}

static int fill_animation(struct fill *f, const sg_rom_object *row)
{
    const uint8_t *src = source(f, row, 24, NULL);
    const sg_rom_object *values, *index;
    struct Animation *out = (struct Animation *)row->out;
    if (!src) return 0;
    values = addressed_row(f, row->owner, SG_ROM_S16, be32(src + 12));
    index = addressed_row(f, row->owner, SG_ROM_U16, be32(src + 16));
    if (!values || !index) return 0;
    if (!f->write) return 1;
    out->flags = (s16)be16(src);
    out->animYTransDivisor = (s16)be16(src + 2);
    out->startFrame = (s16)be16(src + 4);
    out->loopStart = (s16)be16(src + 6);
    out->loopEnd = (s16)be16(src + 8);
    out->unusedBoneCount = (s16)be16(src + 10);
    out->values = (s16 *)values->out;
    out->index = (u16 *)index->out;
    out->length = be32(src + 20);
    return 1;
}

static int check_animation_table(struct fill *f, const sg_rom_object *row)
{
    const uint8_t *src = source(f, row, (size_t)row->count * 4u, NULL);
    const uintptr_t *table = (const uintptr_t *)row->tpl;
    uint32_t i;
    if (!src) return 0;
    for (i = 0; i < row->count; ++i)
        if (!same_pointer(f, row->owner, table[i], be32(src + i * 4u), 0)) return 0;
    return 1;
}

/* One display list: each template tag reads the ROM command(s) it names and
 * writes its GFXCMD words in place of the template's. */
static int fill_gfx(struct fill *f, const sg_rom_object *row)
{
    size_t available, at = 0;
    const uint8_t *src = source(f, row, 8, &available);
    const intptr_t *tpl = (const intptr_t *)row->tpl;
    intptr_t *out = (intptr_t *)row->out, word[10];
    uint32_t i = 0, n, k;
    if (!src) return 0;
    while (i < row->count) {
        intptr_t tag = tpl[i];
        uint32_t w0, w1, op, words = 1;
        unsigned first, second;
        if (at + 8 > available) return 0;
        w0 = be32(src + at); w1 = be32(src + at + 4); op = w0 >> 24;
        at += 8;
        memset(word, 0, sizeof(word));
        switch (tag) {
        case SG_T_VTX:
            /* F3D: (n-1)<<20 | v0<<16 | n*16; v0 is the first index loaded. */
            n = ((w0 >> 20) & 15u) + 1u;
            if (op != 0x04 || (w0 & 0xFFFFu) != n * 16u || !same_pointer(f, row->owner, (uintptr_t)tpl[i + 1], w1, 0))
                return 0;
            word[0] = GFXCMD_VertexData; word[1] = tpl[i + 1]; word[2] = (intptr_t)n; word[3] = (w0 >> 16) & 15u;
            words = 4;
            break;
        case SG_T_TRI1: case SG_T_TRI2:
            /* F3D has no two-triangle command: gsSP2Triangles is two G_TRI1. */
            for (k = 0; k < (tag == SG_T_TRI2 ? 2u : 1u); ++k) {
                unsigned a = (w1 >> 16) & 255u, b = (w1 >> 8) & 255u, c = w1 & 255u;
                if (k) {
                    if (at + 8 > available) return 0;
                    w0 = be32(src + at); w1 = be32(src + at + 4); op = w0 >> 24; at += 8;
                    a = (w1 >> 16) & 255u; b = (w1 >> 8) & 255u; c = w1 & 255u;
                }
                if (op != 0xBF || (w0 & 0xFFFFFFu) || a % 10u || b % 10u || c % 10u) return 0;
                word[k * 5] = GFXCMD_Triangle;
                word[k * 5 + 1] = a / 10u; word[k * 5 + 2] = b / 10u; word[k * 5 + 3] = c / 10u;
                word[k * 5 + 4] = w1 >> 24;
            }
            words = tag == SG_T_TRI2 ? 10 : 5;
            break;
        case SG_T_END:
            if (op != 0xB8) return 0;
            word[0] = GFXCMD_EndDisplayList;
            break;
        case SG_T_DL: case SG_T_BRANCH:
            if (op != 0x06 || ((w0 >> 16) & 255u) != (tag == SG_T_BRANCH ? 1u : 0u)
                    || !same_pointer(f, row->owner, (uintptr_t)tpl[i + 1], w1, 0)) return 0;
            word[0] = GFXCMD_SubDisplayList; word[1] = tpl[i + 1];
            words = 2;
            if (tag == SG_T_BRANCH) { word[2] = GFXCMD_EndDisplayList; words = 3; }
            break;
        case SG_T_LIGHT:
            /* G_MOVEMEM to G_MV_L0 + (n-1)*2, a 16-byte Light. */
            n = (w0 >> 16) & 255u;
            if (op != 0x03 || n < 0x86 || (n - 0x86) & 1u || (w0 & 0xFFFFu) != 16u
                    || !same_pointer(f, row->owner, (uintptr_t)tpl[i + 1], w1, 0)) return 0;
            word[0] = GFXCMD_Light; word[1] = tpl[i + 1]; word[2] = (intptr_t)((n - 0x86) / 2u + 1u);
            words = 3;
            break;
        case SG_T_TEXTURE:
            if (op != 0xBB) return 0;
            word[0] = GFXCMD_Texture; word[1] = w1 >> 16; word[2] = w1 & 0xFFFFu; word[3] = w0 & 255u;
            words = 4;
            break;
        case SG_T_TIMG:
            if (op != 0xFD) return 0;
            word[0] = GFXCMD_SetTextureImage; word[1] = tpl[i + 1];
            words = 2;
            break;
        case SG_T_TILESIZE:
            if (op != 0xF2) return 0;
            word[0] = GFXCMD_SetTileSize;
            word[1] = (w0 >> 12) & 0xFFFu; word[2] = w0 & 0xFFFu;
            word[3] = (w1 >> 12) & 0xFFFu; word[4] = w1 & 0xFFFu;
            words = 5;
            break;
        case SG_T_COMBINE:
            if (op != 0xFC || !sg_combiner_material(w0 & 0xFFFFFFu, w1, 0, &first)
                    || !sg_combiner_material(w0 & 0xFFFFFFu, w1, 1, &second)) return 0;
            word[0] = GFXCMD_Material; word[1] = first; word[2] = second;
            words = 3;
            break;
        case SG_T_SETTILE:
            if (op != 0xF5) return 0;
            word[0] = GFXCMD_SetTile;
            word[1] = (w1 >> 24) & 7u;   /* tile */
            word[2] = (w1 >> 18) & 3u;   /* cmt */
            word[3] = (w1 >> 14) & 15u;  /* maskt */
            word[4] = (w1 >> 10) & 15u;  /* shiftt */
            word[5] = (w1 >> 8) & 3u;    /* cms */
            word[6] = (w1 >> 4) & 15u;   /* masks */
            word[7] = w1 & 15u;          /* shifts */
            words = 8;
            break;
        case SG_T_GEOSET: case SG_T_GEOCLR:
            if (op != (tag == SG_T_GEOSET ? 0xB7u : 0xB6u)) return 0;
            word[0] = GFXCMD_GeometryMode; word[1] = tag == SG_T_GEOSET; word[2] = (intptr_t)w1;
            words = 3;
            break;
        case SG_T_LOADTEX: {
            /* gsDPLoadTextureBlock: image, load tile, sync, load, sync, render tile, size. */
            static const uint8_t ops[7] = {0xFD, 0xF5, 0xE6, 0xF3, 0xE7, 0xF5, 0xF2};
            if (op != ops[0] || at + 6 * 8 > available) return 0;
            for (k = 1; k < 7; ++k, at += 8) if (src[at] != ops[k]) return 0;
            word[0] = GFXCMD_None;
            break;
        }
        default:
            if ((tag & ~(intptr_t)0xFF) != SG_T_SKIP || op != (uint32_t)(tag & 0xFF)) return 0;
            word[0] = GFXCMD_None;
            break;
        }
        if (i + words > row->count) return 0;
        /* A template holds nothing but its tag and pointer operand. */
        for (k = 1; k < words; ++k) {
            int operand = k == 1 && (tag == SG_T_VTX || tag == SG_T_DL || tag == SG_T_BRANCH
                                     || tag == SG_T_LIGHT || tag == SG_T_TIMG);
            if (!operand && tpl[i + k] != 0) return 0;
        }
        if (f->write) memcpy(out + i, word, words * sizeof(intptr_t));
        i += words;
    }
    return 1;
}

/* One geo layout: a tag word converts the ROM's word, a pointer word keeps
 * the compiled pointer once the ROM's names the same row (or, in a function
 * slot, any address in the game's code). */
static int fill_geo(struct fill *f, const sg_rom_object *row)
{
    size_t available, at = 0;
    const uint8_t *src = source(f, row, 4, &available);
    const uintptr_t *tpl = (const uintptr_t *)row->tpl;
    uintptr_t *out = (uintptr_t *)row->out;
    uint32_t i, o = 0;
    if (!src) return 0;
    for (i = 0; i < row->count; ++i) {
        uintptr_t tag = tpl[i], value;
        const uint8_t *w;
        if (at + 4 > available) return 0;
        w = src + at;
        at += 4;
        if (tag == 0 || tag >= SG_G_TAG_LIMIT) {
            if (!same_pointer(f, row->owner, tag, be32(w), 1)) return 0;
            value = tag;
        } else switch (tag & 0xF000u) {
        case SG_G_BBH:
            if (w[0] != (tag & 0xFFu)) return 0;
            value = (uintptr_t)w[0] | ((uintptr_t)w[1] << 8) | ((uintptr_t)be16(w + 2) << 16);
            break;
        case SG_G_BBBB:
            value = (uintptr_t)w[0] | ((uintptr_t)w[1] << 8) | ((uintptr_t)w[2] << 16) | ((uintptr_t)w[3] << 24);
            break;
        case SG_G_HH:
            value = (uintptr_t)be16(w) | ((uintptr_t)be16(w + 2) << 16);
            break;
        case SG_G_W:
            value = be32(w);
            break;
        case SG_G_SHADOW_AS_START:
            /* GEO_SHADOW is 8 bytes; GEO_NODE_START() is CMD_BBH(0x0B, 0, 0). */
            if (tag != SG_G_SHADOW_AS_START || w[0] != 0x16 || at + 4 > available) return 0;
            at += 4;
            value = 0x0B;
            break;
        case SG_G_SKIP:
            if (w[0] != (tag & 0xFFu)) return 0;
            continue;
        default:
            return 0;
        }
        if (f->write) out[o] = value;
        ++o;
    }
    return 1;
}

static int fill_row(struct fill *f, const sg_rom_object *row)
{
    switch (row->kind) {
    case SG_ROM_VTX: return fill_vtx(f, row);
    case SG_ROM_LIGHTS: return fill_lights(f, row);
    case SG_ROM_S16: case SG_ROM_U16: return fill_halves(f, row);
    case SG_ROM_ANIM: return fill_animation(f, row);
    case SG_ROM_ANIM_TABLE: return check_animation_table(f, row);
    case SG_ROM_GFX: return fill_gfx(f, row);
    case SG_ROM_GEO: return fill_geo(f, row);
    default: return 0;
    }
}

int sg_rom_data_load(const uint8_t *rom, size_t bytes, int write, unsigned *held_failed)
{
    struct fill *f = calloc(1, sizeof(*f));
    unsigned t, i, failed = 0;
    if (!f) return SG_OUT_OF_MEMORY;
    f->rom = rom; f->bytes = bytes; f->write = write;
    for (t = 0; t < sg_rom_table_count; ++t)
        for (i = 0; i < *sg_rom_tables[t].count; ++i) {
            const sg_rom_object *row = &sg_rom_tables[t].rows[i];
            if (failed & (1u << row->owner)) continue;
            if (!fill_row(f, row)) {
                failed |= 1u << row->owner;
#ifdef SG_ROM_TRACE
                fprintf(stderr, "rom row failed: owner %u kind %u %#x\n", row->owner, row->kind, row->address);
#endif
            }
        }
    for (i = 0; i < f->block_count; ++i) free(f->blocks[i].owned);
    free(f);
    *held_failed = failed & ~1u;
    return (failed & 1u) ? SG_INVALID_ROM : SG_OK;
}

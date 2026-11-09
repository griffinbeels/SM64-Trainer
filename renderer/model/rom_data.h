/* The model's data rows: every vertex array, light, display list, geo layout
 * and animation the model draws with, filled at open from the player's own
 * ROM (rom_data.c). No Nintendo model, list, light or animation data is
 * compiled in: tools/build_model.py turns each decomp definition into an
 * empty array of its size plus one row naming where the ROM keeps it, and
 * each display list and geo layout into a template (rom_gfx_template.h,
 * rom_geo_template.h) that holds only the shape of its commands and the
 * pointers between them. The fill checks every ROM command against that
 * shape and every ROM pointer against the row the template points to, so a
 * cartridge whose model is not the expected one is refused, never drawn.
 */
#ifndef SG_ROM_DATA_H
#define SG_ROM_DATA_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

enum sg_rom_kind {
    SG_ROM_VTX = 1,     /* Vtx[count] from 16-byte big-endian vertices */
    SG_ROM_LIGHTS,      /* one Lights1, its 24 bytes */
    SG_ROM_S16,         /* s16[count] (animation values) */
    SG_ROM_U16,         /* u16[count] (animation indices) */
    SG_ROM_ANIM,        /* one struct Animation; its pointers name S16/U16 rows */
    SG_ROM_ANIM_TABLE,  /* a compiled table of Animation pointers, checked only */
    SG_ROM_GFX,         /* a display list: count template words, as many out */
    SG_ROM_GEO          /* a geo layout: count template words, at most as many out */
};

typedef struct sg_rom_object {
    uint8_t kind, owner;  /* owner: 0 Mario, else the sg_held_kind */
    uint32_t block;       /* the US ROM offset of the block (rom_blocks in assets.c) */
    uint32_t address;     /* segmented address in the US ROM (decomp address + US shift) */
    uint32_t count;
    void *out;
    const void *tpl;
} sg_rom_object;

typedef struct sg_rom_table { const sg_rom_object *rows; const unsigned *count; } sg_rom_table;
extern const sg_rom_table sg_rom_tables[];
extern const unsigned sg_rom_table_count;

/* Display-list template words (rom_gfx_template.h): a tag, then the words its
 * GFXCMD form takes, pointer operands in place, the rest zero. */
enum sg_rom_gfx_tag {
    SG_T_VTX = 1, SG_T_TRI1, SG_T_TRI2, SG_T_END, SG_T_BRANCH, SG_T_DL, SG_T_LIGHT,
    SG_T_TEXTURE, SG_T_TIMG, SG_T_TILESIZE, SG_T_COMBINE, SG_T_SETTILE, SG_T_GEOSET,
    SG_T_GEOCLR, SG_T_LOADTEX, SG_T_SKIP = 0x100 /* | the F3D opcode */
};
/* Geo template words (rom_geo_template.h). Below 0x10000 a word is a tag;
 * 0 or above is a pointer the layout keeps (NULL, a list, a layout or a
 * function). No pointer lies in the first 64 KiB of a Win32 process. */
enum sg_rom_geo_tag {
    SG_G_BBH = 0x1000 /* | opcode */, SG_G_BBBB = 0x2000, SG_G_HH = 0x3000, SG_G_W = 0x4000,
    SG_G_SHADOW_AS_START = 0x5000, SG_G_SKIP = 0x6000 /* | opcode */, SG_G_TAG_LIMIT = 0x10000
};

/* Fill (write) or only check (!write) every row from rom. Returns SG_OK, or
 * SG_INVALID_ROM when one of Mario's own rows does not read; *held_failed
 * gets a bit per held kind with a row that did not. */
int sg_rom_data_load(const uint8_t *rom, size_t bytes, int write, unsigned *held_failed);
#ifdef __cplusplus
}
#endif
#endif

/* Included after gfx_macros.h by a generated list unit (tools/build_model.py):
 * each list macro becomes a template of exactly as many words as its GFXCMD
 * form, a tag (rom_data.h) and its pointer operands, every number left to
 * rom_data.c to read from the ROM's command. A macro missing here would
 * compile its numbers in, so the generator refuses any list macro outside
 * TEMPLATE_MACROS, and the native data scan checks the built files. */
#include "rom_data.h"

#undef gsSPVertex
#undef gsSP2Triangles
#undef gsSP1Triangle
#undef gsSPEndDisplayList
#undef gsSPBranchList
#undef gsSPDisplayList
#undef gsSPLight
#undef gsSPTexture
#undef gsDPSetTextureImage
#undef gsDPSetTileSize
#undef gsDPPipeSync
#undef gsDPLoadSync
#undef gsDPTileSync
#undef gsDPLoadBlock
#undef gsDPSetEnvColor
#undef gsDPSetAlphaCompare
#undef gsDPSetCombineMode
#undef gsDPSetTile
#undef gsSPSetGeometryMode
#undef gsSPClearGeometryMode
#undef gsDPLoadTextureBlock

#define gsSPVertex(v, n, v0) SG_T_VTX, (intptr_t)(v), 0, 0
#define gsSP2Triangles(v00, v01, v02, flag0, v10, v11, v12, flag1) SG_T_TRI2, 0, 0, 0, 0, 0, 0, 0, 0, 0
#define gsSP1Triangle(v00, v01, v02, flag0) SG_T_TRI1, 0, 0, 0, 0
#define gsSPEndDisplayList() SG_T_END
#define gsSPBranchList(dl) SG_T_BRANCH, (intptr_t)(dl), 0
#define gsSPDisplayList(dl) SG_T_DL, (intptr_t)(dl)
#define gsSPLight(l, n) SG_T_LIGHT, (intptr_t)(l), 0
#define gsSPTexture(s, t, level, tile, on) SG_T_TEXTURE, 0, 0, 0
/* The atlas cell is the model's own numbering, not the ROM's address. */
#define gsDPSetTextureImage(f, s, w, i) SG_T_TIMG, (intptr_t)(i)
#define gsDPSetTileSize(t, uls, ult, lrs, lrt) SG_T_TILESIZE, 0, 0, 0, 0
#define gsDPPipeSync() (SG_T_SKIP | 0xE7)
#define gsDPLoadSync() (SG_T_SKIP | 0xE6)
#define gsDPTileSync() (SG_T_SKIP | 0xE8)
#define gsDPLoadBlock(tile, uls, ult, lrs, dxt) (SG_T_SKIP | 0xF3)
#define gsDPSetEnvColor(r, g, b, a) (SG_T_SKIP | 0xFB)
#define gsDPSetAlphaCompare(type) (SG_T_SKIP | 0xB9)
#define gsDPSetCombineMode(a, b) SG_T_COMBINE, 0, 0
#define gsDPSetTile(fmt, siz, line, tmem, tile, palette, cmt, maskt, shiftt, cms, masks, shifts) \
    SG_T_SETTILE, 0, 0, 0, 0, 0, 0, 0
#define gsSPSetGeometryMode(word) SG_T_GEOSET, 0, 0
#define gsSPClearGeometryMode(word) SG_T_GEOCLR, 0, 0
/* The shorthand's seven ROM commands stay one GFXCMD_None, as compiled before. */
#define gsDPLoadTextureBlock(timg, fmt, siz, width, height, pal, cms, cmt, masks, maskt, shifts, shiftt) SG_T_LOADTEX

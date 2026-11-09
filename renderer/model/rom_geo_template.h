/* Included by a generated geo unit (tools/build_model.py) before the decomp's
 * layouts; rom_geo_template_end.h restores the macros for our own wrappers.
 * Every geo macro is built from these four, so each word becomes a tag
 * naming how rom_data.c converts the ROM's big-endian word (the command's
 * opcode kept, to check the ROM's), and a pointer stays as compiled. */
#include "rom_data.h"
#pragma push_macro("CMD_BBH")
#pragma push_macro("CMD_BBBB")
#pragma push_macro("CMD_HH")
#pragma push_macro("CMD_W")
#undef CMD_BBH
#undef CMD_BBBB
#undef CMD_HH
#undef CMD_W
#define CMD_BBH(a, b, c) ((uintptr_t)(SG_G_BBH | ((a) & 0xFF)))
#define CMD_BBBB(a, b, c, d) ((uintptr_t)SG_G_BBBB)
#define CMD_HH(a, b) ((uintptr_t)SG_G_HH)
#define CMD_W(a) ((uintptr_t)SG_G_W)
/* A held graph's shadow (8 bytes in the ROM) is drawn as a plain start node. */
#define SG_GEO_SHADOW_AS_START() ((uintptr_t)SG_G_SHADOW_AS_START)
/* A ROM command the template leaves out (the Owl's unmatched close). */
#define SG_GEO_SKIP(opcode) ((uintptr_t)(SG_G_SKIP | (opcode)))

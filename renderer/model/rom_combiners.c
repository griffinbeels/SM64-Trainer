/* The combiner modes the model's lists use, encoded by the SDK's own gbi.h
 * macros (this unit alone sees its Gfx; the lists see libsm64's). rom_data.c
 * matches a ROM gsDPSetCombineMode against them, one cycle at a time, to the
 * material code gfx_macros.h gives the mode's name. */
#include "decomp/include/PR/gbi.h"
#include "model.h"
#include "rom_combiners.h"

/* Spelled out: a macro parameter would expand the mode's eight selectors
 * before the SDK's macro could split it. */
static const struct { Gfx command; unsigned material; } modes[] = {
    { gsDPSetCombineMode(G_CC_SHADE, G_CC_SHADE), SG_MATERIAL_SOLID },
    { gsDPSetCombineMode(G_CC_SHADEFADEA, G_CC_SHADEFADEA), SG_MATERIAL_SOLID },
    { gsDPSetCombineMode(G_CC_BLENDRGBA, G_CC_BLENDRGBA), SG_MATERIAL_ALPHA_MIX },
    { gsDPSetCombineMode(G_CC_BLENDRGBFADEA, G_CC_BLENDRGBFADEA), SG_MATERIAL_ALPHA_MIX },
    { gsDPSetCombineMode(G_CC_DECALRGBA, G_CC_DECALRGBA), SG_MATERIAL_DECAL_RGBA },
    { gsDPSetCombineMode(G_CC_DECALFADEA, G_CC_DECALFADEA), SG_MATERIAL_DECAL_RGBA },
    { gsDPSetCombineMode(G_CC_DECALFADE, G_CC_DECALFADE), SG_MATERIAL_DECAL_RGB },
    { gsDPSetCombineMode(G_CC_MODULATERGB, G_CC_MODULATERGB), SG_MATERIAL_MODULATE_RGB },
    { gsDPSetCombineMode(G_CC_MODULATERGBFADEA, G_CC_MODULATERGBFADEA), SG_MATERIAL_MODULATE_RGB },
    { gsDPSetCombineMode(G_CC_MODULATERGBA, G_CC_MODULATERGBA), SG_MATERIAL_MODULATE_RGBA },
};

/* Each cycle's selector bits in the G_SETCOMBINE words (GCCc0w0..GCCc1w1). */
static const uint32_t cycle_w0[2] = { 0x00FFFE00u, 0x000001FFu };
static const uint32_t cycle_w1[2] = { 0xF000FE00u, 0x0FFC01FFu };

int sg_combiner_material(uint32_t w0, uint32_t w1, unsigned cycle, unsigned *material)
{
    unsigned i;
    for (i = 0; i < sizeof(modes) / sizeof(modes[0]); ++i)
        if ((((uint32_t)modes[i].command.words.w0 ^ w0) & cycle_w0[cycle]) == 0
                && (((uint32_t)modes[i].command.words.w1 ^ w1) & cycle_w1[cycle]) == 0) {
            *material = modes[i].material;
            return 1;
        }
    return 0;
}

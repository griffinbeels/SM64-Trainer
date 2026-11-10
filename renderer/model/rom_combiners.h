#ifndef SG_ROM_COMBINERS_H
#define SG_ROM_COMBINERS_H
#include <stdint.h>
/* The material code of one cycle (0 or 1) of a G_SETCOMBINE command's words
 * (w0 without its opcode byte); 0 for a mode the model does not support. */
int sg_combiner_material(uint32_t w0, uint32_t w1, unsigned cycle, unsigned *material);
#endif

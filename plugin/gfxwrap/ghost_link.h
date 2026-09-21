/* Pinned GLideN64 glue. rom() runs on the serialized emulation caller after
 * preceding renderer commands finish; raster/lifetime calls run on renderer. */
#pragma once
#include <stdint.h>
namespace ghost_link {
void rom(bool opened);
void context_created();
void context_lost();
bool begin_list();
void end_list(bool completed);
void projection();
void geometry(uint32_t before, uint32_t after);
void color_target_changed();
void triangles(uint32_t count);
bool begin_hud();
void end_hud();
void clear_target();
}

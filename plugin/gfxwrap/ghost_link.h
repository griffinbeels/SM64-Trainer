/* Pinned GLideN64 glue. All entrypoints run on its serialized renderer thread. */
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
}

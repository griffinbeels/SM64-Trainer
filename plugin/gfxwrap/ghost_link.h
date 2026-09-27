/* Pinned GLideN64 glue. rom() runs on the serialized emulation caller after
 * preceding renderer commands finish; raster/lifetime calls run on renderer. */
#pragma once
#include <stdint.h>
struct FrameBuffer;
namespace ghost_link {
// The real bound raster target; FBE-off uses GLideN64's screen-size FBO.
FrameBuffer* raster_buffer();
// The renderer's name for a buffer's attachments: its color texture and depth
// object. The compositor reuses what it verified while the name holds.
uint64_t attachments(const FrameBuffer*);
void rom(bool opened);
void context_created();
void context_lost();
bool begin_list();
void end_list(bool completed);
void projection();
void display_list(uint32_t dl, uint32_t physical_dl);
void geometry(uint32_t before, uint32_t after);
void color_target_changed();
void triangles(uint32_t count);
bool begin_hud();
void end_hud();
// Translucent world layer: after a world triangle draw, mirror it once per
// pass (0 color, 1 depth) while begin_layer returns true; see ghost_compositor.
bool begin_layer(uint32_t pass);
void end_layer();
void clear_target();
}

/* Render-thread composition; the worker owns models/clip storage. */
#pragma once
#include "ghost_pass.h"
#include "ghost_capture.h"
namespace ghost_draw {
void begin(uint32_t rom_generation, uint32_t configuration);
void observe(const ghost::RasterTarget&);
void seal(const gc_sample&, const ghost::WorldPass&);
void finish(const ghost::WorldPass*);
void cancel();
bool begin_hud();
void end_hud();
bool begin_layer(uint32_t pass, uint32_t framebuffer);
void end_layer();
void break_layer();
void context_lost();
// This list can draw a ghost (playback drawing on the prepared model).
bool drawing();
}

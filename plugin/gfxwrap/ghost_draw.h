/* Render-thread composition; the worker owns models/clip storage. */
#pragma once
#include "ghost_pass.h"
#include "ghost_capture.h"
struct gs_hook_cost;
namespace ghost_draw {
// sample: the list's own, for playback to choose this list's ghosts (null: none).
void begin(uint32_t rom_generation, uint32_t configuration, const gc_sample *sample);
void observe(const ghost::RasterTarget&);
void seal(const gc_sample&, const ghost::WorldPass&);
void finish(const ghost::WorldPass*);
void cancel();
bool begin_hud();
void end_hud();
bool begin_layer(uint32_t pass, uint32_t framebuffer, uint64_t attachments);
void end_layer();
// Ends one mirrored surface: the layer's gateway scope, both passes, closes.
void close_layer();
bool layer_open();
void break_layer();
void context_lost();
// This list draws a ghost: playback chose one at its start (ghost_playback::plan).
bool drawing();
// Renderer thread, once per list: adds what drawing the ghost cost this list
// (stages, driver queries, gateway error reads) and starts the next count.
void take_cost(gs_hook_cost &);
}

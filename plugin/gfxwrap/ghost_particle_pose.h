/* Recorded Mario particles -> world-space quads for the compositor, under the
 * same live view-projection as their ghost's body. Each executed particle
 * matrix loses its RECORDED camera; a view-aligned (billboard) one is aligned
 * to the LIVE camera instead, as the game billboards it every frame. Only the
 * ROM frames the model read (model particles.c) are drawn; others are
 * skipped and counted. No GL, allocation or RAM access. */
#pragma once
#include "ghost_capture.h"
#include "ghost_compositor.h"
#include "model.h"
namespace ghost_particle_pose {
struct Placed { uint32_t drawn, unknown, invalid; };
/* out holds ghost_compositor::particle_vertices. Clears result first. */
Placed place(sg_model *, const sg_particle_view &, const gc_sample &recorded, const gc_camera &live,
             const float view_projection[16], ghost_compositor::ParticleVertex *out,
             ghost_compositor::Particles &result);
}

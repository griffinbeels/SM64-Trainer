/* Mario's particles as the game executed them, for one serialized, completed
 * F3D occurrence. A particle is any active object whose parent chain reaches
 * Mario's object (bubbles, splash and its droplets, water rings, dust,
 * sparkles, flames): its first display list pushed with a modelview whose
 * translation is that object's fresh cameraToObject is recorded with the
 * executed matrix. Another object's bubble or a coin's sparkle never
 * matches. Reads RAM only; no GL, allocation or model knowledge. */
#pragma once
#include "ghost_capture.h"

struct gc_particle_probe {
    const uint8_t *ram = nullptr;
    uint32_t ram_bytes = 0;
    uint32_t count = 0; // Mario-descended objects found this occurrence
    uint32_t objects[64]{};
    float camera_to_object[64][3]{};
    bool recorded[64]{};
    bool prepared = false, overflow = false;
    gc_particles particles{};
};

/* mario_object is the Layout's gMarioObject variable, object_pool the first
 * pool slot (both KSEG0). Clears the probe; a bad pointer leaves it
 * unprepared, and an unprepared probe publishes nothing. */
void ghost_particles_prepare(gc_particle_probe &, const uint8_t *ram, uint32_t ram_bytes,
                             uint32_t mario_object, uint32_t object_pool);
void ghost_particles_display_list(gc_particle_probe &, uint32_t dl, const float matrix[16]);
/* Publishes GC_VALID_PARTICLES (count may be 0) only for a completed occurrence. */
void ghost_particles_finish(gc_particle_probe &, bool completed, gc_sample &);

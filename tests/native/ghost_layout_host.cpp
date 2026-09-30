/* Prints every name of src/sm64_events/ghost/native_layout.py from the compiled
 * headers (sizeof, offsetof and the macros themselves), as one JSON object.
 * tests/test_ghost_layout.py compares the two; headers only, no RAM or GL. */
#include "ghost_ipc.h"
#include "ghost_clip_upgrade.h"
#include "ghost_tuning.h"
#include "ghost_playback.h"
#include <cstdio>

static bool first = true;
static void name(const char *key) { std::printf("%s\"%s\":", first ? "" : ",", key); first = false; }
static void number(const char *key, double value) { name(key); std::printf("%.17g", value); }
static void text(const char *key, const char *value) { name(key); std::printf("\"%s\"", value); }
static void wide(const char *key, const wchar_t *value) {
    name(key); std::printf("\"");
    for (; *value; ++value) std::printf(*value == L'\\' ? "\\\\" : "%c", char(*value));
    std::printf("\"");
}
#define SAMPLE_AT(field) (offsetof(gclip_frame, sample) + offsetof(gc_sample, field))
#define POSE_FIELD_AT(field) (SAMPLE_AT(pose) + offsetof(gc_pose, field))

int main() {
    std::printf("{");
    text("CLIP_MAGIC", GCLIP_MAGIC); number("CLIP_VERSION", GCLIP_VERSION);
    number("SAMPLE_VERSION", GC_VERSION);
    text("IPC_MAGIC", GIPC_MAGIC); number("IPC_VERSION", GIPC_VERSION); wide("IPC_PREFIX", GIPC_PREFIX);
    number("GP_WRONG_SCENE", GP_WRONG_SCENE);

    number("HEADER_BYTES", sizeof(gclip_header)); number("RECORD_BYTES", sizeof(gclip_frame));
    number("SAMPLE_BYTES", sizeof(gc_sample)); number("RECORD_PREFIX_BYTES", offsetof(gclip_frame, sample));
    number("LAYOUT_BYTES", sizeof(gc_layout)); number("CLOCK_BYTES", sizeof(gcc_output));
    number("SHADOW_BYTES", sizeof(gc_shadow)); number("SOUND_BYTES", sizeof(gc_sound));
    number("PARTICLES_BYTES", sizeof(gc_particles)); number("PARTICLE_BYTES", sizeof(gc_particle));
    number("HELD_BYTES", sizeof(gc_held));

    number("POSE_AT", SAMPLE_AT(pose)); number("ROOT_KIND_AT", POSE_FIELD_AT(root_kind));
    number("REFUSAL_AT", POSE_FIELD_AT(refusal)); number("ROOT_MATRIX_AT", POSE_FIELD_AT(root_matrix));
    number("CAMERA_AT", SAMPLE_AT(camera)); number("CAMERA_FOV_AT", SAMPLE_AT(camera) + offsetof(gc_camera, fov));
    number("WARP_AT", SAMPLE_AT(pending_warp_op)); number("CLOCK_AT", SAMPLE_AT(clock));
    number("SHADOW_AT", SAMPLE_AT(shadow)); number("SOUND_AT", SAMPLE_AT(sound));
    number("PARTICLES_AT", SAMPLE_AT(particles)); number("HELD_AT", SAMPLE_AT(held));

    number("SHADOW_POSITION_AT", offsetof(gc_shadow, position)); number("SHADOW_ALPHA_AT", offsetof(gc_shadow, alpha));
    number("SHADOW_RESERVED_AT", offsetof(gc_shadow, reserved)); number("SHADOW_TEXTURE_AT", offsetof(gc_shadow, texture));
    number("SOUND_BITS_AT", offsetof(gc_sound, bits));
    number("PARTICLES_ENTRIES_AT", offsetof(gc_particles, entries));
    number("PARTICLE_OPACITY_AT", offsetof(gc_particle, opacity));
    number("PARTICLE_TRANSLATION_AT", offsetof(gc_particle, translation));
    number("HELD_HOLP_AT", offsetof(gc_held, holp)); number("HELD_ANGLES_AT", offsetof(gc_held, angles));
    number("HELD_RELEASED_AT", offsetof(gc_held, released));

    number("SHADOW_VERTICES", GC_SHADOW_VERTICES); number("SHADOW_TEXTURE_BYTES", GC_SHADOW_TEXTURE_BYTES);
    number("SOUND_EVENTS", GC_SOUND_EVENTS); number("SOUND_OVERFLOW", GC_SOUND_OVERFLOW);
    number("PARTICLE_EVENTS", GC_PARTICLES); number("PARTICLE_OVERFLOW", GC_PARTICLES_OVERFLOW);
    number("PARTICLE_OPACITY", GC_PARTICLE_OPACITY);
    number("VALID_CLOCK", GC_VALID_CLOCK); number("VALID_POSE", GC_VALID_POSE); number("VALID_CAMERA", GC_VALID_CAMERA);
    number("VALID_ACTION", GC_VALID_ACTION); number("VALID_WARP", GC_VALID_WARP); number("VALID_SHADOW", GC_VALID_SHADOW);
    number("VALID_SOUND", GC_VALID_SOUND); number("VALID_PARTICLES", GC_VALID_PARTICLES); number("VALID_HELD", GC_VALID_HELD);
    number("VALID_REQUIRED", GC_VALID_REQUIRED); number("VALID_ALL", GC_VALID_ALL);
    number("MAX_RECORDS", GCLIP_MAX_RECORDS); number("WORLD_READY", GCLIP_WORLD_READY);
    number("DANCE_TAIL", GCLIP_DANCE_TAIL);
    number("ZERO_RAW", GCLIP_ZERO_RAW);
    number("STAR_CLOCK", GCLIP_STAR_CLOCK); number("SEGMENT_CLOCK", GCLIP_SEGMENT_CLOCK);
    number("ROOT_TRS", GC_ROOT_TRS); number("ROOT_SCALED_MODELVIEW", GC_ROOT_SCALED_MODELVIEW);

    text("V4_MAGIC", ghost_clip::v4_magic); number("V4_VERSION", ghost_clip::v4_version);
    number("V4_SAMPLE_VERSION", ghost_clip::v4_sample_version);
    number("V4_RECORD_BYTES", ghost_clip::v4_record_bytes); number("V4_SAMPLE_BYTES", ghost_clip::v4_sample_bytes);
    text("V5_MAGIC", ghost_clip::v5_magic); number("V5_VERSION", ghost_clip::v5_version);
    number("V5_SAMPLE_VERSION", ghost_clip::v5_sample_version);
    number("V5_RECORD_BYTES", ghost_clip::v5_record_bytes); number("V5_SAMPLE_BYTES", ghost_clip::v5_sample_bytes);
    number("RELEASED_FRAMES", GC_RELEASED_FRAMES);

    number("IPC_SLOTS", GIPC_SLOTS); number("IPC_RING_AT", offsetof(gipc_page, records));
    number("IPC_PATH_CHARS", GIPC_PATH_CHARS);
    number("GHOSTS", GP_GHOSTS); number("PROXIMITY_MAX", GP_PROXIMITY_MAX); number("BRIDGE_FRAMES", GP_BRIDGE_FRAMES);
    std::printf("}\n");
    return 0;
}

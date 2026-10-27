/* The scene viewer's ghosts (M4 lab): saved recordings drawn into a recorded
 * scene's pictures by the viewer's host (tools/scene_lab/viewer_host.c), which
 * renders a picture on request, in any order. Each picture names every
 * ghost's frame, so nothing carries from one picture to the next: a seek or a
 * step back draws exactly what playing forward to it draws. A ghost is drawn
 * by saved playback's own figure (ghost_figure.h: look, shadow, particles,
 * name box), hidden behind the world by the same compositor, and chosen the
 * way playback chooses a row (gaps and refused rows hide, a short refusal is
 * bridged, a star clip's dance tail holds and fades out).
 *
 * The host calls the table below from its plugin thread, between lists; the
 * renderer's calls (ghost_draw.cpp) run on GLideN64's own thread, which draws
 * each list while the host's call waits. A lock hands each picture's ghosts
 * across, and setup's model passes to that thread on its first call. Project64
 * never calls setup, so in the game every renderer call answers "nothing" and
 * the game's ghosts draw exactly as before. Docs: ghost-native-runtime.md,
 * "Ghosts in the scene viewer". */
#pragma once
#include "ghost_capture.h"
#include "ghost_tuning.h"
#include <stdint.h>

#define GV_VERSION 1u
#define GV_GHOSTS GP_GHOSTS
#ifndef __cdecl
#if !defined(_MSC_VER)
#define __cdecl
#endif
#endif

enum gv_result {
    GV_OK = 0, GV_INVALID = -1, GV_MODEL = -2, GV_CLIP = -3, GV_MEMORY = -4,
    GV_LAYOUT = -5, GV_WRONG_THREAD = -6, GV_NOT_READY = -7
};
/* gv_look.flags: the ghost settings a viewer ghost follows (gp_request's
 * fields of the same names). The overlap and player-first fades, the corner
 * input stack, the path and the sound are the live race's, never drawn here. */
enum gv_look_flags {
    GV_TINT = 1u, GV_SHADOW = 2u, GV_NAMES = 4u, GV_INPUTS = 8u, GV_VALUES = 16u,
    GV_WHITE_NAME = 32u /* the name's box is white (GP_NAME_WHITE), else black */
};
#pragma pack(push, 4)
typedef struct gv_look {
    float opacity, name_background_opacity;
    uint32_t flags, reserved;
} gv_look;
typedef struct gv_ghost {
    uint32_t slot;        /* a loaded slot, 0..GV_GHOSTS-1; each at most once a picture */
    int32_t frame;        /* game frames since the stage's first frame: the ghost's own row */
    uint32_t hue_degrees; /* 0..359, its tint under GV_TINT */
    uint32_t reserved;
} gv_ghost;
/* What the last picture's list did with its ghosts. */
typedef struct gv_status {
    uint32_t bytes;
    uint32_t asked;     /* ghosts the picture named */
    uint32_t planned;   /* 1: the overlay reached the ghosts' choice (0 with ghosts asked: not set up yet) */
    uint32_t chosen;    /* with a row to show on this frame (not in a gap, past the end or elsewhere) */
    uint32_t posed;     /* drawn into the world pass */
    uint32_t displayed; /* 1: the list's ghost image was finished for the screen */
    uint32_t reserved[2];
} gv_status;
typedef struct gv_api {
    uint32_t bytes, version, reserved[2];
    /* Once, before any picture: the capture layout the stage's camera and
     * scene are read with (ghost_stream's), and the verified 8 MiB cartridge
     * the ghost model, its particles and its name atlas are built from. */
    int32_t (__cdecl *setup)(const gc_layout *, const uint8_t *rom, uint32_t rom_bytes);
    /* Once per add: a saved recording (a .ghost file's bytes, any version
     * saved playback reads) into a slot, with the terminated UTF-8 label its
     * name box shows. Zero bytes empties the slot. A clip playback would
     * refuse is refused here (GV_CLIP) and the slot is left empty. */
    int32_t (__cdecl *load)(uint32_t slot, const uint8_t *clip, uint32_t bytes, const char *label);
    /* Before each picture's list: the ghosts it draws. Zero ghosts draws none
     * and leaves the renderer exactly as a host without ghosts. */
    int32_t (__cdecl *picture)(const gv_look *, const gv_ghost *, uint32_t count);
    int32_t (__cdecl *status)(gv_status *, uint32_t bytes);
} gv_api;
#pragma pack(pop)

#ifdef __cplusplus
#include "model.h"
namespace ghost { struct WorldPass; }
namespace ghost_compositor { struct Pose; }
namespace ghost_annotation { struct View; }
namespace ghost_viewer {
const gv_api *api();
/* Renderer thread. The viewer's model, when it holds one and saved playback
 * holds none (ghost_draw.cpp asks playback first). */
bool atlas(sg_atlas_view *out, uint64_t *model_id);
bool particles(sg_particle_view *out, uint64_t *model_id);
bool annotations(ghost_annotation::View *out, uint64_t *model_id);
/* At list start, from the list's own sample: whether any ghost of the
 * current picture has a row to show (the translucent world layer runs only
 * then). evaluate poses those rows at the seal into out (capacity poses),
 * unsorted; current() holds until the next picture. */
bool plan(const gc_sample &);
bool evaluate(const gc_sample &, const ghost::WorldPass &, ghost_compositor::Pose *out, uint32_t capacity,
              uint32_t &count);
bool current();
/* The list's ghost image was finished (or dropped) for the screen. */
void finished(bool displayed);
}
#endif

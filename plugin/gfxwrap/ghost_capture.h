/* Read-only SM64 pose/camera capture. No GL, physics, transport, or allocation.
 * The caller owns a readable RDRAM span and serializes calls at its DList
 * boundary. Output is a value: publish it even when invalid, never reuse an
 * older valid sample. Source/picture association is the caller's contract.
 */
#ifndef SM64_GHOST_CAPTURE_H
#define SM64_GHOST_CAPTURE_H
#include <stdint.h>
#include "ghost_clock.h"

#ifdef __cplusplus
extern "C" {
#endif

#define GC_VERSION 7u
#define GC_RAM_WORD_SWAPPED 1u
#define GC_ROM_US 1u
#define GC_GRAPH_NODE_LIMIT 256u
#define GC_GRAPH_DEPTH_LIMIT 32u

enum gc_valid {
    GC_VALID_CLOCK = 1u, GC_VALID_POSE = 2u, GC_VALID_CAMERA = 4u,
    GC_VALID_ACTION = 8u, GC_VALID_WARP = 16u,
    /* Optional evidence: never part of GC_OK. */
    GC_VALID_SHADOW = 32u, GC_VALID_SOUND = 64u, GC_VALID_PARTICLES = 128u,
    GC_VALID_HELD = 256u
};
#define GC_VALID_REQUIRED 31u
#define GC_VALID_ALL 511u
#define GC_SHADOW_VERTICES 9u
#define GC_SHADOW_TEXTURE_BYTES 128u /* 16x16 IA8 alpha nibbles, two per byte */
#define GC_SOUND_EVENTS 8u
enum gc_sound_flags { GC_SOUND_OVERFLOW = 1u };
#define GC_PARTICLES 16u
/* A released held object is followed at most this many game frames (10 s):
 * longer than a thrown Bob-omb's flight to its explosion. */
#define GC_RELEASED_FRAMES 300u
#define GC_PARTICLE_BASIS_ONE 4096 /* basis entries are 4.12 fixed point */
enum gc_particle_flags { GC_PARTICLES_OVERFLOW = 1u };
/* dl_shadow_9_verts: the game's eight triangles over the nine vertices.
 * Capture refuses any other executed list; playback draws this order. */
static const uint8_t gc_shadow_triangles[24] = {
    0,3,4, 0,4,1, 1,4,2, 2,4,5, 3,6,4, 4,6,7, 4,7,8, 4,8,5 };
enum gc_status {
    GC_OK = 0, GC_BAD_ARGUMENT = 1, GC_BAD_CONFIG = 2,
    GC_BAD_POINTER = 3, GC_BAD_VALUE = 4, GC_BAD_GRAPH = 5,
    GC_GRAPH_LIMIT = 6, GC_AMBIGUOUS_CAMERA = 7, GC_NO_CAMERA = 8,
    GC_UNSUPPORTED_ROOT = 9, GC_UNSUPPORTED_HELD = 10,
    GC_STALE_SEQUENCE = 11, GC_RESET_REQUIRED = 12, GC_UNSUPPORTED_HEAD = 13
};
/* The one check that refused Mario's pose. Append only: Python and the
 * extraction audit decode by value (clip.py REFUSALS). */
enum gc_refusal {
    GC_REFUSED_NONE = 0,
    GC_REFUSED_OBJECT = 1,          /* MarioState/object/body pointer invalid */
    GC_REFUSED_AREA = 2,            /* Mario belongs to another area */
    GC_REFUSED_HELD = 3,            /* holding or riding (TRS path only) */
    GC_REFUSED_BILLBOARD = 4,
    GC_REFUSED_THROW_MATRIX = 5,    /* throwMatrix still set after rendering */
    GC_REFUSED_FLOOR = 6,           /* floor-aligned action (TRS path only) */
    GC_REFUSED_HEAD = 7,            /* C-up head override */
    GC_REFUSED_TRANSFORM = 8,       /* nonfinite position or zero/huge scale */
    GC_REFUSED_ANIMATION = 9,       /* no animation header */
    GC_REFUSED_ANIMATION_RANGE = 10,/* id or frame outside the loaded animation */
    GC_REFUSED_ANIMATION_STALE = 11,/* animTimer: the renderer did not process Mario */
    GC_REFUSED_BODY_STATE = 12,     /* action or body byte out of range */
    GC_REFUSED_CAMERA = 13,         /* no valid camera, so no root proof */
    GC_REFUSED_CAMERA_TO_OBJECT = 14,
    GC_REFUSED_SHARED_MODEL = 15,   /* another active object uses Mario's model */
    GC_REFUSED_GRAPH = 16,          /* shadow/scale/ASM node shape differs */
    GC_REFUSED_BODY_GRAPH = 17,     /* selected body branch not walkable */
    GC_REFUSED_MATERIAL = 18,       /* retired 2026-09-23: cap effects are proven */
    GC_REFUSED_NOT_DRAWN = 19,      /* no Mario alpha list executed (culled) */
    GC_REFUSED_NOT_DRAWN_FLOOR = 20,/* not drawn while floor-aligned and moved: tilt unknown */
    GC_REFUSED_BODY_MISMATCH = 21,  /* alpha executed, body did not follow */
    GC_REFUSED_DUPLICATE = 22,      /* Mario's lists matched twice */
    GC_REFUSED_INCOMPLETE = 23,     /* interrupted list or foreign occurrence */
    GC_REFUSED_LAST = 23
};
enum gc_root_kind {
    GC_ROOT_UNRESOLVED = 0, GC_ROOT_TRS = 1,
    /* Executed F3D pre-animation matrix: gfx scale and Mario's .25 geo
     * scale, followed by the recorded camera. Never an unqualified pointer. */
    GC_ROOT_SCALED_MODELVIEW = 2
};

/* Addresses are N64 KSEG0 addresses from the selected Python Layout. No
 * arbitrary pointer programs or offsets cross this ABI. New fields remain
 * VERIFY until their live address gates pass. Only the US schema is enabled.
 * Either warp root may be zero to declare unavailable raw warp evidence;
 * nonzero roots must still be aligned and bounded inside RDRAM.
 */
#pragma pack(push, 4)
typedef struct gc_layout {
    uint32_t bytes, version, ram_order, rdram_bytes, rom_layout;
    uint32_t global_timer, mario_state, mario_object, current_area_pointer;
    uint32_t area_update_counter, current_level, current_area, igt_overall;
    uint32_t object_pool, pending_warp_op, delayed_warp_timer;
    /* play_sound's code, its u8 producer, the audio thread's u8 consumer and
     * the 256-entry request ring. All zero declares sound unavailable; any
     * nonzero set must be complete. The code must match the ring layout. */
    uint32_t sound_code, sound_producer, sound_consumer, sound_ring;
    gcc_config clock;
    /* Segment 0x13's RAM base (layout.py behaviour_base): a held object's
     * behaviour is stored segmented, never as a per-version pointer. Zero
     * declares held-object capture unavailable. */
    uint32_t behaviour_base;
} gc_layout;

typedef struct gc_pose {
    float position[3], scale[3];
    int16_t angles[3], animation_id, animation_frame, anim_y_translation;
    uint16_t anim_timer, gfx_flags;
    uint32_t action, body_action, mario_flags, root_kind;
    /* Post-callback body angles. The model applies rotation XYZ = (Y,Z,X).
     * C-up overrides graph rotations without updating body.headAngle and is
     * refused; head_angles is never guessed from that stale body field.
     */
    int16_t torso_angles[3], head_angles[3];
    uint16_t model_state;
    uint8_t cap_state, eye_state, hand_state, wing_flutter;
    uint8_t punch_state, grab_position, hidden;
    /* gc_refusal: why THIS frame's executed lists did not prove the pose.
     * Without GC_VALID_POSE it is the only nonzero byte of the pose block.
     * With it, zero means proven this frame; GC_REFUSED_NOT_DRAWN means the
     * game culled Mario and the root is either carried from the last proven
     * frame (SCALED_MODELVIEW; sample.camera is then the camera it executed
     * under) or his own TRS (see ghost_root.h). */
    uint8_t refusal;
    float root_matrix[16]; /* zero for TRS; native Mat4 order, translation 12..14 */
} gc_pose;

/* The shadow the game actually drew under Mario this frame: its executed
 * G_MTX (same Mat4 order as root_matrix), the nine executed Vtx values
 * (positions relative to that matrix, S10.5 texture coordinates, alpha) and
 * the IA8 texture it sampled. Intensity is proven zero, so only alpha
 * nibbles are kept. Zero unless GC_VALID_SHADOW. */
typedef struct gc_shadow {
    float matrix[16];
    int16_t position[GC_SHADOW_VERTICES][3];
    int16_t uv[GC_SHADOW_VERTICES][2];
    uint8_t alpha[GC_SHADOW_VERTICES];
    uint8_t reserved;
    uint8_t texture[GC_SHADOW_TEXTURE_BYTES];
} gc_shadow;

/* play_sound requests whose source is Mario's own object, issued since the
 * previous captured frame (global timer exactly one earlier). producer and
 * consumer are the raw ring counters at this boundary. */
typedef struct gc_sound {
    uint8_t count, flags, producer, consumer;
    uint32_t bits[GC_SOUND_EVENTS];
} gc_sound;

/* Mario's particles as executed this frame (ghost_particle_capture.h): each
 * object's first pushed display list (segmented, e.g. 0x0401DD60 bubble) and
 * its executed modelview: basis columns in 4.12 fixed point, translation in
 * view space. Count may be zero; beyond 16 the nearest Mario are kept, nearest
 * first, with OVERFLOW set. Zero unless GC_VALID_PARTICLES. */
typedef struct gc_particle {
    uint32_t display_list;
    int16_t basis[9]; /* column-major 3x3 */
    /* GC_PARTICLE_OPACITY | alpha when the game's generated list set this
     * object's opacity (G_SETENVCOLOR 255,255,255,alpha) before it; else 0. */
    uint16_t opacity;
    float translation[3];
} gc_particle;
#define GC_PARTICLE_OPACITY 0x100u
typedef struct gc_particles {
    uint8_t count, flags, reserved[2];
    gc_particle entries[GC_PARTICLES];
} gc_particles;

/* The object Mario holds, as geo_process_held_object drew it from his right
 * hand this frame. Its own graph node never draws while held: it is off, or
 * (the Bob-omb, Mips) on but invisible, culled after geo_process_object
 * advanced its animation. A current animTimer (== area_update_counter) proves
 * this frame's animation either way, and the hand's callback writes the HOLP:
 * this frame's world root. An object without
 * an animation (a box: animation_id -1, frame 0) has no timer; the hand draws
 * it whenever Mario's body draws, so it is kept only beside a root his
 * executed lists proved this frame. The orientation is Mario's own root (the
 * hand copies his throwMatrix), scaled by `scale`.
 *
 * RELEASED (`released` 1): the same object after it left his hand, thrown or
 * dropped, drawn by its own node: TRS(`holp` = its gfx position, `angles`,
 * `scale`), as geo_process_object composes it without a throwMatrix. The
 * reader follows the slot while its behaviour is unchanged and it stays
 * active, one game frame at a time, for at most GC_RELEASED_FRAMES. A frame
 * its node is off or invisible (culled, or not yet shown) carries nothing.
 * Zero unless GC_VALID_HELD. */
typedef struct gc_held {
    uint32_t behavior; /* segmented behaviour script, e.g. 0x13003174 bhvBobomb */
    int16_t animation_id, animation_frame, anim_y_translation;
    uint16_t anim_state; /* oAnimState: texture/switch frame (the Bob-omb's blink) */
    float holp[3];     /* marioBodyState->heldObjLastPosition */
    float scale[3];    /* the held object's gfx scale */
    int16_t angles[3]; /* released: its gfx angle; zero in his hand */
    uint16_t released;
} gc_held;

typedef struct gc_camera {
    float position[3], focus[3];
    int16_t roll, roll_screen;
    float fov;
    int16_t near_plane, far_plane;
} gc_camera;

/* Fixed-width value packet, little-endian host scalars / IEEE binary32.
 * No pointers, implicit host-size integers, or libsm64 structs. A zero valid
 * bit means that component has been cleared. status is the first refusal.
 * The renderer must attach its actual world projection/viewport separately.
 */
typedef struct gc_sample {
    uint32_t bytes, version, epoch, valid_fields;
    uint64_t sequence, dlist_serial;
    int64_t qpc;
    uint32_t global_timer;
    uint16_t area_update_counter, igt;
    int16_t level, area;
    uint32_t status;
    /* Current gMarioState action, independently valid under GC_VALID_ACTION.
     * Remains available when graphics pose/body/root capture is unsupported.
     * Requires a nonzero action and MarioState belonging to the active area.
     */
    uint32_t live_action;
    gc_pose pose;
    gc_camera camera;
    /* Independently valid raw game warp fields; no classifier runs here.
     * Both clear if either Layout root is unavailable. The complete u16/i16
     * ranges are preserved; a pending-op whitelist would discard evidence.
     */
    uint16_t pending_warp_op;
    int16_t delayed_warp_timer;
    /* Always zero from ghost_capture_read; the source-thread clock owner
     * attaches derived evidence to this same immutable occurrence afterward.
     */
    gcc_output clock;
    gc_shadow shadow;
    gc_sound sound;
    gc_particles particles;
    gc_held held;
} gc_sample;

/* Zero-initialize or reset before use. One reader per serialized producer.
 * A backwards game clock requires a new epoch; invalid samples never restore
 * an old pose. No camera pointer cache survives an area change or reset.
 */
typedef struct gc_reader {
    uint32_t epoch, last_global_timer, have_clock, reset_required;
    uint64_t last_sequence, last_dlist_serial;
    /* Sound baseline: the producer seen at global timer sound_timer. */
    uint32_t sound_baseline, sound_timer, sound_producer, reserved;
    /* The object in his hand at held_timer, then the one released from it:
     * RAM address, behaviour pointer, the last game frame followed and how
     * many were followed. Zero address: none. */
    uint32_t held_object, held_behaviour, held_timer;
    uint32_t released_object, released_behaviour, released_timer, released_frames, reserved2;
} gc_reader;
#pragma pack(pop)

/* Process-local arguments; this struct is NOT a wire format. The byte span
 * must remain readable and unchanged for the duration of this bounded call.
 */
typedef struct gc_frame {
    const uint8_t *rdram;
    uint32_t rdram_bytes, epoch;
    uint64_t sequence, dlist_serial;
    int64_t qpc;
    /* Renderer-local segment bases for this occurrence (gSP.segment), never
     * a RAM address or part of the stored ABI. Needed when oAnimations or
     * its entries are segmented; NULL refuses that optional object. */
    const uint32_t *segments;
} gc_frame;

void ghost_capture_reset(gc_reader *reader);
uint32_t ghost_capture_validate_layout(const gc_layout *layout);
/* Returns GC_OK only when all five required components are valid; shadow
 * and sound are optional evidence with their own bits. Output is cleared
 * first, including on malformed arguments. Inspect valid_fields for partial
 * clock/camera/action evidence when the live Mario pose is unsupported.
 */
uint32_t ghost_capture_read(gc_reader *reader, const gc_layout *layout,
                           const gc_frame *frame, gc_sample *out);
struct gc_root_probe;
/* Same reader, with deferred root evidence supplied only by the executing
 * renderer. Until root_finish succeeds, unsupported roots remain cleared. */
uint32_t ghost_capture_begin(gc_reader *reader, const gc_layout *layout,
                            const gc_frame *frame, gc_sample *out, struct gc_root_probe *probe);

#ifdef __cplusplus
}
#endif
#endif

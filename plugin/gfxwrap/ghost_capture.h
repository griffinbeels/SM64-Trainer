/* Read-only SM64 pose/camera capture. No GL, physics, transport, or allocation.
 * The caller owns a readable RDRAM span and serializes calls at its DList
 * boundary. Output is a value: publish it even when invalid, never reuse an
 * older valid sample. Source/picture association is the caller's contract.
 */
#ifndef SM64_GHOST_CAPTURE_H
#define SM64_GHOST_CAPTURE_H
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define GC_VERSION 1u
#define GC_RAM_WORD_SWAPPED 1u
#define GC_ROM_US 1u
#define GC_GRAPH_NODE_LIMIT 256u
#define GC_GRAPH_DEPTH_LIMIT 32u

enum gc_valid { GC_VALID_CLOCK = 1u, GC_VALID_POSE = 2u, GC_VALID_CAMERA = 4u };
enum gc_status {
    GC_OK = 0, GC_BAD_ARGUMENT = 1, GC_BAD_CONFIG = 2,
    GC_BAD_POINTER = 3, GC_BAD_VALUE = 4, GC_BAD_GRAPH = 5,
    GC_GRAPH_LIMIT = 6, GC_AMBIGUOUS_CAMERA = 7, GC_NO_CAMERA = 8,
    GC_UNSUPPORTED_ROOT = 9, GC_UNSUPPORTED_HELD = 10,
    GC_STALE_SEQUENCE = 11, GC_RESET_REQUIRED = 12, GC_UNSUPPORTED_HEAD = 13
};
enum gc_root_kind { GC_ROOT_UNRESOLVED = 0, GC_ROOT_TRS = 1 };

/* Addresses are N64 KSEG0 addresses from the selected Python Layout. No
 * arbitrary pointer programs or offsets cross this ABI. New fields remain
 * VERIFY until their live address gates pass. Only the US schema is enabled.
 */
#pragma pack(push, 4)
typedef struct gc_layout {
    uint32_t bytes, version, ram_order, rdram_bytes, rom_layout;
    uint32_t global_timer, mario_state, mario_object, current_area_pointer;
    uint32_t area_update_counter, current_level, current_area, igt_overall;
    uint32_t object_pool, reserved[2];
} gc_layout;

typedef struct gc_pose {
    float position[3], scale[3];
    int16_t angles[3], animation_id, animation_frame, anim_y_translation;
    uint16_t anim_timer, gfx_flags;
    uint32_t action, body_action, mario_flags, root_kind;
    /* Post-callback body angles. The model applies rotation XYZ = (Y,Z,X).
     * C-up overrides graph rotations without updating body.headAngle and is
     * refused in V1; head_angles is never guessed from that stale body field.
     */
    int16_t torso_angles[3], head_angles[3];
    uint16_t model_state;
    uint8_t cap_state, eye_state, hand_state, wing_flutter;
    uint8_t punch_state, grab_position, hidden, reserved;
} gc_pose;

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
    gc_pose pose;
    gc_camera camera;
} gc_sample;

/* Zero-initialize or reset before use. One reader per serialized producer.
 * A backwards game clock requires a new epoch; invalid samples never restore
 * an old pose. No camera pointer cache survives an area change or reset.
 */
typedef struct gc_reader {
    uint32_t epoch, last_global_timer, have_clock, reset_required;
    uint64_t last_sequence, last_dlist_serial;
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
} gc_frame;

void ghost_capture_reset(gc_reader *reader);
uint32_t ghost_capture_validate_layout(const gc_layout *layout);
/* Returns GC_OK only when all three components are valid. Output is cleared
 * first, including on malformed arguments. Inspect valid_fields for partial
 * clock/camera evidence when the live Mario pose is unsupported.
 */
uint32_t ghost_capture_read(gc_reader *reader, const gc_layout *layout,
                           const gc_frame *frame, gc_sample *out);

#ifdef __cplusplus
}
#endif
#endif

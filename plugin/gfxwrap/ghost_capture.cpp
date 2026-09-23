#include "ghost_capture.h"
#include "ghost_root.h"
#include "ghost_ram.h"
#include <cstddef>
#include <cmath>
#include <cstring>

namespace {
using ghost::kseg0;
constexpr uint32_t object_size = 0x260, object_count = 240;
constexpr uint16_t active = 1, invisible = 16, billboard = 4;
constexpr uint16_t root_type = 1, perspective_type = 0x103, camera_type = 0x114;
constexpr uint16_t object_type = 0x18, object_parent_type = 0x29, held_type = 0x12e;
// Original N64 types.h / graph_node.h offsets, NOT host libsm64 offsetof.
constexpr uint32_t graph_next = 8, graph_parent = 12, graph_children = 16;
constexpr uint32_t area_root = 4, area_camera = 0x24;
constexpr uint32_t mario_body = 0x98, mario_area = 0x90, mario_object = 0x88, mario_animations = 0xa0;
constexpr uint32_t mario_animation_count = 209;

bool span(uint32_t address, uint32_t count, uint32_t size, uint32_t alignment = 4) {
    return ghost::ram_span(address, count, size, alignment);
}
using Ram = ghost::Ram;

bool finite(float value, float limit = 10000000.0f) {
    return std::isfinite(value) && value >= -limit && value <= limit;
}

struct GraphWalk {
    Ram &ram;
    uint32_t camera;
    uint32_t visited[GC_GRAPH_NODE_LIMIT]{};
    uint32_t count = 0, found = 0, perspective = 0;

    uint32_t chain(uint32_t first, uint32_t parent, uint32_t depth, uint32_t projection) {
        if (!first) return GC_OK;
        if (depth >= GC_GRAPH_DEPTH_LIMIT) return GC_GRAPH_LIMIT;
        uint32_t node = first;
        do {
            if (count == GC_GRAPH_NODE_LIMIT) return GC_GRAPH_LIMIT;
            for (uint32_t i = 0; i < count; ++i) if (visited[i] == node) return GC_BAD_GRAPH;
            visited[count++] = node;
            if (!ram.has(node, 20)) return GC_BAD_POINTER;
            if (ram.u32(node + graph_parent) != parent) return GC_BAD_GRAPH;
            const auto type = ram.u16(node), flags = ram.u16(node + 2);
            const auto next = ram.u32(node + graph_next), child = ram.u32(node + graph_children);
            if (flags & active) {
                if (type == camera_type) {
                    if (!ram.has(node, 0x3c)) return GC_BAD_POINTER;
                    if (ram.u32(node + 0x18) == camera) {
                        if (!projection) return GC_BAD_GRAPH;
                        if (found) return GC_AMBIGUOUS_CAMERA;
                        found = node; perspective = projection;
                    }
                } else if (type != object_type && type != object_parent_type && type != held_type) {
                    if (type == perspective_type && !ram.has(node, 0x24)) return GC_BAD_POINTER;
                    const auto result = chain(child, node, depth + 1,
                                              type == perspective_type ? node : projection);
                    if (result != GC_OK) return result;
                }
            }
            if (!next) return GC_BAD_GRAPH; // SM64 sibling lists are circular.
            node = next;
        } while (node != first);
        return GC_OK;
    }
};

uint32_t capture_camera(Ram &ram, uint32_t area, int16_t area_index, gc_camera &out) {
    if (!ram.has(area, 0x28)) return GC_BAD_POINTER;
    const uint32_t root = ram.u32(area + area_root), camera = ram.u32(area + area_camera);
    if (!ram.has(root, 0x24) || !ram.has(camera, 0x6c)) return GC_BAD_POINTER;
    if (ram.u16(root) != root_type || !(ram.u16(root + 2) & active)
            || ram.u8(root + 0x14) != area_index) return GC_BAD_GRAPH;
    GraphWalk walk{ram, camera};
    walk.visited[walk.count++] = root;
    const auto result = walk.chain(ram.u32(root + graph_children), root, 0, 0);
    if (result != GC_OK) return result;
    if (!walk.found) return GC_NO_CAMERA;
    for (unsigned i = 0; i < 3; ++i) {
        out.position[i] = ram.f32(walk.found + 0x1c + 4*i);
        out.focus[i] = ram.f32(walk.found + 0x28 + 4*i);
        if (!finite(out.position[i]) || !finite(out.focus[i])) return GC_BAD_VALUE;
    }
    out.roll = ram.s16(walk.found + 0x38);
    out.roll_screen = ram.s16(walk.found + 0x3a);
    out.fov = ram.f32(walk.perspective + 0x1c);
    out.near_plane = ram.s16(walk.perspective + 0x20);
    out.far_plane = ram.s16(walk.perspective + 0x22);
    double distance = 0;
    for (unsigned i = 0; i < 3; ++i) {
        const double delta = double(out.position[i]) - out.focus[i]; distance += delta*delta;
    }
    if (!ram.ok) return GC_BAD_POINTER;
    if (distance < 0.000001 || !finite(out.fov, 179.0f) || out.fov <= 0
            || out.near_plane <= 0 || out.far_plane <= out.near_plane) return GC_BAD_VALUE;
    return GC_OK;
}

// play_sound(bits, pos): the 48 bytes of the original US leaf function. The
// two lui/addiu pairs must name exactly the configured producer and ring;
// the rest proves an 8-byte stride, bits at +0, source at +4, and a u8
// producer published after both stores. Another build refuses sound.
bool sound_code_matches(Ram &ram, const gc_layout &layout) {
    const auto upper = [](uint32_t address) { return (address + 0x8000u) >> 16; };
    const auto lower = [](uint32_t address) { return address & 0xffffu; };
    const uint32_t expected[12] = {
        0x3c060000u | upper(layout.sound_producer), 0x24c60000u | lower(layout.sound_producer),
        0x90c20000u,
        0x3c0f0000u | upper(layout.sound_ring), 0x25ef0000u | lower(layout.sound_ring),
        0x000270c0u, 0x01cf1821u, 0x24580001u, 0xac640000u, 0xac650004u, 0x03e00008u, 0xa0d80000u,
    };
    for (unsigned i = 0; i < 12; ++i)
        if (ram.u32(layout.sound_code + 4*i) != expected[i]) return false;
    return ram.ok;
}

// Mario's requests pass gMarioObject->header.gfx.cameraToObject as source.
constexpr uint32_t sound_source_offset = 0x54;

void capture_sound(Ram &ram, const gc_layout &layout, gc_reader &reader, gc_sample &out) {
    const bool baseline = reader.sound_baseline != 0;
    const uint32_t previous_timer = reader.sound_timer, previous = reader.sound_producer;
    reader.sound_baseline = 0;
    if (!layout.sound_code || !sound_code_matches(ram, layout)) return;
    const uint8_t producer = ram.u8(layout.sound_producer), consumer = ram.u8(layout.sound_consumer);
    const uint32_t object = ram.u32(layout.mario_object);
    if (!ram.ok) return;
    reader.sound_baseline = 1; reader.sound_timer = out.global_timer; reader.sound_producer = producer;
    // Requests between two captures belong to exactly one game frame only
    // when that frame immediately follows; a skipped list has no attribution.
    if (!baseline || out.global_timer != previous_timer + 1u
            || object < layout.object_pool || (object - layout.object_pool) % object_size
            || (object - layout.object_pool) / object_size >= object_count) return;
    gc_sound sound{};
    sound.producer = producer; sound.consumer = consumer;
    const uint32_t issued = (producer - previous) & 0xffu;
    for (uint32_t i = 0; i < issued; ++i) {
        const uint32_t entry = layout.sound_ring + 8u * ((previous + i) & 0xffu);
        const uint32_t bits = ram.u32(entry), source = ram.u32(entry + 4);
        if (source != object + sound_source_offset) continue; // another object's sound
        if (sound.count < GC_SOUND_EVENTS) sound.bits[sound.count++] = bits;
        else sound.flags |= GC_SOUND_OVERFLOW;
    }
    if (!ram.ok) return;
    out.sound = sound; out.valid_fields |= GC_VALID_SOUND;
}


// Each refusal names its one check in gc_pose.refusal (reason).
// The animation Mario's DMA buffer holds: gMarioState->animList
// {dmaTable, currentAddr, bufTarget} names the loaded entry by its source
// address, dmaTable {count, srcAddr, {offset, size}[count]} names each entry.
// -1 unless `drawn` is that buffer and exactly one entry matches. Asks with
// fits(), so a bad list leaves the pose's own refusal (animation_range).
int16_t loaded_animation(Ram &ram, uint32_t state, uint32_t drawn) {
    const uint32_t list = ram.u32(state + mario_animations);
    if (!ram.fits(list, 12)) return -1;
    const uint32_t table = ram.u32(list), loaded = ram.u32(list + 4);
    if (ram.u32(list + 8) != drawn || !ram.fits(table, 8)) return -1;
    const uint32_t count = ram.u32(table), source = ram.u32(table + 4);
    if (!count || count > mario_animation_count || !ram.fits(table + 8, count * 8)) return -1;
    int16_t found = -1;
    for (uint32_t i = 0; i < count; ++i) {
        if (source + ram.u32(table + 8 + 8*i) != loaded) continue;
        if (found >= 0) return -1;
        found = int16_t(i);
    }
    return found;
}

uint32_t capture_pose(Ram &ram, const gc_layout &layout, uint32_t area,
                      int16_t area_index, uint16_t visual_frame, gc_pose &out, bool deferred,
                      uint8_t &reason) {
    const uint32_t state = layout.mario_state;
    const uint32_t object = ram.u32(layout.mario_object);
    reason = GC_REFUSED_OBJECT;
    if (!ram.has(state, mario_animations + 4) || !ram.has(object, object_size)
            || object < layout.object_pool || (object - layout.object_pool) % object_size
            || (object - layout.object_pool) / object_size >= object_count) return GC_BAD_POINTER;
    const uint32_t body = ram.u32(state + mario_body);
    if (!ram.has(body, 0x20)) return GC_BAD_POINTER;
    reason = GC_REFUSED_AREA;
    if (ram.u32(state + mario_object) != object || ram.u32(state + mario_area) != area
            || ram.u8(object + 0x18) != area_index) return GC_BAD_VALUE;
    out.action = ram.u32(state + 0x0c); out.body_action = ram.u32(body);
    out.mario_flags = ram.u32(state + 4);
    out.gfx_flags = ram.u16(object + 2);
    // A held object (hands-free holding included) or ridden shell is drawn
    // by its own lists after Mario's alpha/body pair, so the executed root
    // proof does not depend on it. Only the TRS path keeps this refusal.
    reason = GC_REFUSED_HELD;
    if (!deferred && (ram.u32(state + 0x7c) || ram.u32(state + 0x84))) return GC_UNSUPPORTED_HELD;
    reason = uint8_t((out.gfx_flags & billboard) ? GC_REFUSED_BILLBOARD
        : ram.u32(object + 0x50) ? GC_REFUSED_THROW_MATRIX : GC_REFUSED_FLOOR);
    if ((out.gfx_flags & billboard) || ram.u32(object + 0x50)
            || (!deferred && (ghost_floor_aligned(out.action) || ghost_floor_aligned(out.body_action)
                             || (ram.u32(object + 0x8c) & 0xa00)))) return GC_UNSUPPORTED_ROOT;
    const uint32_t camera = ram.u32(area + area_camera);
    reason = GC_REFUSED_OBJECT;
    if (!ram.has(camera, 1, 1)) return GC_BAD_POINTER;
    reason = GC_REFUSED_HEAD;
    if (ram.u8(camera) == 6) return GC_UNSUPPORTED_HEAD;
    reason = GC_REFUSED_TRANSFORM;
    for (unsigned i = 0; i < 3; ++i) {
        out.position[i] = ram.f32(object + 0x20 + 4*i);
        out.scale[i] = ram.f32(object + 0x2c + 4*i);
        out.angles[i] = ram.s16(object + 0x1a + 2*i);
        out.torso_angles[i] = ram.s16(body + 0x0c + 2*i);
        out.head_angles[i] = ram.s16(body + 0x12 + 2*i);
        if (!finite(out.position[i]) || !finite(out.scale[i], 100.0f)
                || std::fabs(out.scale[i]) < 0.000001f) return GC_BAD_VALUE;
    }
    out.animation_id = ram.s16(object + 0x38);
    out.anim_y_translation = ram.s16(object + 0x3a);
    out.animation_frame = ram.s16(object + 0x40);
    out.anim_timer = ram.u16(object + 0x42);
    const uint32_t animation = ram.u32(object + 0x3c);
    reason = GC_REFUSED_ANIMATION;
    if (!ram.has(animation, 0x18)) return GC_BAD_POINTER;
    const int16_t end = ram.s16(animation + 8);
    // A jump, long jump, backflip or steep jump writes animID -1 so its own
    // animation restarts. When the air step lands that same frame (every BLJ
    // press, a jump off a slide slope) nothing sets it again, and the renderer
    // draws curAnim, Mario's DMA buffer, still holding what it last loaded.
    if (out.animation_id == -1) out.animation_id = loaded_animation(ram, state, animation);
    out.model_state = ram.u16(body + 8);
    out.cap_state = ram.u8(body + 4); out.eye_state = ram.u8(body + 5);
    out.hand_state = ram.u8(body + 6); out.wing_flutter = ram.u8(body + 7);
    out.grab_position = ram.u8(body + 0x0a); out.punch_state = ram.u8(body + 0x0b);
    out.hidden = !(out.gfx_flags & active) || (out.gfx_flags & invisible) ? 1 : 0;
    reason = GC_REFUSED_OBJECT;
    if (!ram.ok) return GC_BAD_POINTER;
    // The renderer skips an inactive Mario, so his animation never advances
    // from set_mario_animation's startFrame-1 (act_disappeared leaves -1). A
    // hidden pose draws nothing; playback never evaluates its animation.
    reason = GC_REFUSED_ANIMATION_RANGE;
    if (!out.hidden && (out.animation_id < 0 || out.animation_id > 208
            || end <= 0 || out.animation_frame < 0 || out.animation_frame >= end))
        return GC_BAD_VALUE;
    reason = GC_REFUSED_ANIMATION_STALE;
    if (!out.hidden && out.anim_timer != visual_frame) return GC_BAD_VALUE;
    reason = GC_REFUSED_BODY_STATE;
    if (!out.action || !out.body_action
            || out.cap_state > 3 || out.eye_state > 8 || out.hand_state > 5
            || out.wing_flutter > 1 || out.model_state > 0x3ff || out.grab_position > 3
            || (out.punch_state >> 6) > 2 || (out.punch_state & 0x3f) > 5) return GC_BAD_VALUE;
    out.root_kind = deferred && !out.hidden ? GC_ROOT_UNRESOLVED : GC_ROOT_TRS;
    reason = GC_REFUSED_NONE;
    return GC_OK;
}

// Segment 0x13 holds every behaviour script (the US map's last is below 0x6000).
constexpr uint32_t behaviour_segment = 0x13000000u, behaviour_span = 0x10000u;
constexpr uint32_t object_behaviour = 0x20c, object_active_flags = 0x74, object_anim_state = 0xf0, body_holp = 0x18;

// gMarioState->heldObj as geo_process_held_object drew it from Mario's hand
// this frame (gc_held). Anything unprovable leaves the block cleared.
void capture_held(Ram &ram, const gc_layout &layout, gc_sample &out) {
    // Rides a pose only; ghost_root_finish clears it with a refused root.
    if (!layout.behaviour_base || !(out.valid_fields & GC_VALID_POSE)) return;
    const uint32_t state = layout.mario_state, held = ram.u32(state + 0x7c), body = ram.u32(state + mario_body);
    if (!held || held < layout.object_pool || (held - layout.object_pool) % object_size
            || (held - layout.object_pool) / object_size >= object_count || !ram.has(body, 0x24)) return;
    // Alive, drawn only through the hand, with a model the hand can draw
    // (Bowser's tail anchor has none). Its own node never draws while held:
    // either it is off (cur_obj_disable_rendering, most holdables) or it stays
    // on but invisible (bobomb_held_loop, bhv_mips_held set only
    // GRAPH_RENDER_INVISIBLE; obj_is_in_view then culls it after
    // geo_process_object has already advanced its animation this frame).
    const uint16_t node_flags = ram.u16(held + 2);
    if (!(ram.u16(held + object_active_flags) & 1u) || ((node_flags & active) && !(node_flags & invisible))
            || !ram.u32(held + 0x14)) return;
    // Fresh this frame: its animation globals were set this frame, by the hand
    // or (invisible, still active) by its own culled node.
    // Without an animation, ghost_root_finish keeps it only beside a root
    // Mario's executed lists proved.
    const uint32_t animation = ram.u32(held + 0x3c);
    if (animation && (!ram.has(animation, 0x18) || ram.u16(held + 0x42) != out.area_update_counter)) return;
    const uint32_t behaviour = ram.u32(held + object_behaviour);
    if (behaviour < layout.behaviour_base || behaviour - layout.behaviour_base >= behaviour_span) return;
    gc_held h{};
    h.behavior = behaviour_segment + (behaviour - layout.behaviour_base);
    h.animation_id = animation ? ram.s16(held + 0x38) : int16_t(-1);
    h.anim_y_translation = animation ? ram.s16(held + 0x3a) : int16_t(0);
    h.animation_frame = animation ? ram.s16(held + 0x40) : int16_t(0);
    const uint32_t anim_state = ram.u32(held + object_anim_state);
    if (anim_state > 255) return;
    h.anim_state = uint16_t(anim_state);
    const int16_t end = animation ? ram.s16(animation + 8) : int16_t(1);
    if ((animation && h.animation_id < 0) || end <= 0 || h.animation_frame < 0 || h.animation_frame >= end) return;
    for (unsigned i = 0; i < 3; ++i) {
        h.holp[i] = ram.f32(body + body_holp + 4*i);
        h.scale[i] = ram.f32(held + 0x2c + 4*i);
        if (!finite(h.holp[i]) || !finite(h.scale[i], 100.0f) || std::fabs(h.scale[i]) < 0.000001f) return;
    }
    if (!ram.ok) return;
    out.held = h; out.valid_fields |= GC_VALID_HELD;
}
} // namespace

extern "C" void ghost_capture_reset(gc_reader *reader) {
    if (reader) std::memset(reader, 0, sizeof(*reader));
}

extern "C" uint32_t ghost_capture_validate_layout(const gc_layout *layout) {
    if (!layout || layout->bytes != sizeof(*layout) || layout->version != GC_VERSION
            || layout->ram_order != GC_RAM_WORD_SWAPPED || layout->rom_layout != GC_ROM_US
            || (layout->rdram_bytes != (4u << 20) && layout->rdram_bytes != (8u << 20))) return GC_BAD_CONFIG;
    const uint32_t words[] = {layout->global_timer, layout->mario_object, layout->current_area_pointer};
    for (auto address : words) if (!span(address, 4, layout->rdram_bytes)) return GC_BAD_CONFIG;
    const uint32_t halves[] = {layout->area_update_counter, layout->current_level,
                              layout->current_area, layout->igt_overall};
    for (auto address : halves) if (!span(address, 2, layout->rdram_bytes, 2)) return GC_BAD_CONFIG;
    if (!span(layout->mario_state, mario_animations + 4, layout->rdram_bytes)
            || !span(layout->object_pool, object_count*object_size, layout->rdram_bytes)) return GC_BAD_CONFIG;
    const uint32_t warps[] = {layout->pending_warp_op, layout->delayed_warp_timer};
    for (auto address : warps)
        if (address && !span(address, 2, layout->rdram_bytes, 2)) return GC_BAD_CONFIG;
    if (layout->sound_code || layout->sound_producer || layout->sound_consumer || layout->sound_ring) {
        if (!span(layout->sound_code, 48, layout->rdram_bytes)
                || !span(layout->sound_producer, 1, layout->rdram_bytes, 1)
                || !span(layout->sound_consumer, 1, layout->rdram_bytes, 1)
                || !span(layout->sound_ring, 256 * 8, layout->rdram_bytes, 8)) return GC_BAD_CONFIG;
    }
    if (layout->behaviour_base && !span(layout->behaviour_base, 4, layout->rdram_bytes)) return GC_BAD_CONFIG;
    gcc_clock validation{};
    if (!gcc_init(&validation, &layout->clock)) return GC_BAD_CONFIG;
    return GC_OK;
}

static uint32_t capture_read(gc_reader *reader, const gc_layout *layout,
                            const gc_frame *frame, gc_sample *out, gc_root_probe *probe) {
    if (probe) *probe = {};
    if (!out) return GC_BAD_ARGUMENT;
    std::memset(out, 0, sizeof(*out)); out->bytes = sizeof(*out); out->version = GC_VERSION;
    if (frame) {
        out->epoch = frame->epoch; out->sequence = frame->sequence;
        out->dlist_serial = frame->dlist_serial; out->qpc = frame->qpc;
    }
    if (!reader || !frame || !frame->rdram || !frame->epoch || !frame->sequence
            || !frame->dlist_serial || frame->qpc < 0) return out->status = GC_BAD_ARGUMENT;
    if (ghost_capture_validate_layout(layout) != GC_OK || frame->rdram_bytes != layout->rdram_bytes)
        return out->status = GC_BAD_CONFIG;
    if (reader->epoch != frame->epoch) {
        ghost_capture_reset(reader); reader->epoch = frame->epoch;
    }
    if (frame->sequence <= reader->last_sequence || frame->dlist_serial <= reader->last_dlist_serial)
        return out->status = GC_STALE_SEQUENCE;
    reader->last_sequence = frame->sequence; reader->last_dlist_serial = frame->dlist_serial;
    if (reader->reset_required) return out->status = GC_RESET_REQUIRED;
    Ram ram{frame->rdram, frame->rdram_bytes};
    out->global_timer = ram.u32(layout->global_timer);
    if (reader->have_clock && out->global_timer < reader->last_global_timer) {
        reader->reset_required = 1; return out->status = GC_RESET_REQUIRED;
    }
    reader->last_global_timer = out->global_timer; reader->have_clock = 1;
    out->area_update_counter = ram.u16(layout->area_update_counter);
    out->igt = ram.u16(layout->igt_overall);
    out->level = ram.s16(layout->current_level); out->area = ram.s16(layout->current_area);
    if (!ram.ok || out->level < 4 || out->level > 36 || out->area < 1 || out->area > 8)
        return out->status = GC_BAD_VALUE;
    out->valid_fields = GC_VALID_CLOCK;
    // Sound needs the game clock of this and the previous captured frame.
    capture_sound(ram, *layout, *reader, *out);
    ram.ok = true;
    if (layout->pending_warp_op && layout->delayed_warp_timer) {
        out->pending_warp_op = ram.u16(layout->pending_warp_op);
        out->delayed_warp_timer = ram.s16(layout->delayed_warp_timer);
        out->valid_fields |= GC_VALID_WARP;
    } else out->status = GC_BAD_VALUE;
    const uint32_t area = ram.u32(layout->current_area_pointer);
    // An unloaded or mismatched area (a level/area transition) has no Mario.
    out->pose.refusal = GC_REFUSED_AREA;
    if (!ram.has(area, 0x28)) return out->status = out->status ? out->status : GC_BAD_POINTER;
    if (ram.u8(area) != out->area || !(ram.u8(area + 1) & active))
        return out->status = out->status ? out->status : GC_BAD_VALUE;
    out->pose.refusal = GC_REFUSED_NONE;
    const uint32_t action = ram.u32(layout->mario_state + 0x0c);
    if (action && ram.u32(layout->mario_state + mario_area) == area) {
        out->live_action = action; out->valid_fields |= GC_VALID_ACTION;
    } else if (!out->status) out->status = GC_BAD_VALUE;
    gc_camera camera{};
    const uint32_t camera_status = capture_camera(ram, area, out->area, camera);
    if (camera_status == GC_OK) { out->camera = camera; out->valid_fields |= GC_VALID_CAMERA; }
    else if (!out->status) out->status = camera_status;
    ram.ok = true;
    gc_pose pose{};
    uint8_t reason = GC_REFUSED_NONE;
    const uint32_t pose_status = capture_pose(ram, *layout, area, out->area, out->area_update_counter,
                                              pose, probe != nullptr, reason);
    if (pose_status == GC_OK) { out->pose = pose; out->valid_fields |= GC_VALID_POSE; }
    else {
        out->pose.refusal = reason;
        if (!out->status) out->status = pose_status;
    }
    ram.ok = true;
    capture_held(ram, *layout, *out);
    ram.ok = true;
    if (probe) ghost_root_prepare(*probe, *frame, ram.u32(layout->mario_object), layout->object_pool, *out);
    return out->status;
}

extern "C" uint32_t ghost_capture_read(gc_reader *reader, const gc_layout *layout,
                                      const gc_frame *frame, gc_sample *out) {
    return capture_read(reader,layout,frame,out,nullptr);
}
extern "C" uint32_t ghost_capture_begin(gc_reader *reader, const gc_layout *layout,
                                       const gc_frame *frame, gc_sample *out, gc_root_probe *probe) {
    return capture_read(reader,layout,frame,out,probe);
}

static_assert(sizeof(gc_layout) == 188 && offsetof(gc_layout, behaviour_base) == 184, "Ghost layout ABI changed");
static_assert(offsetof(gc_layout, sound_code) == 64, "Ghost sound layout ABI changed");
static_assert(offsetof(gc_layout, pending_warp_op) == 56, "Ghost warp layout ABI changed");
static_assert(offsetof(gc_layout, delayed_warp_timer) == 60, "Ghost countdown layout ABI changed");
static_assert(offsetof(gc_layout, clock) == 80, "Ghost clock layout ABI changed");
static_assert(sizeof(gc_particle) == 36 && sizeof(gc_particles) == 580 && offsetof(gc_particle, opacity) == 22,
              "Ghost particle ABI changed");
static_assert(offsetof(gc_sample, particles) == 636, "Ghost particle block follows sound");
static_assert(sizeof(gc_sample) == 1252 && offsetof(gc_sample, held) == 1216 && sizeof(gc_held) == 36,
              "Ghost sample ABI changed");
static_assert(sizeof(gc_shadow) == 292 && sizeof(gc_sound) == 36, "Ghost optional evidence ABI changed");
static_assert(offsetof(gc_sample, shadow) == 308 && offsetof(gc_sample, sound) == 600, "Ghost optional evidence offsets");
static_assert(offsetof(gc_shadow, position) == 64 && offsetof(gc_shadow, uv) == 118
              && offsetof(gc_shadow, alpha) == 154 && offsetof(gc_shadow, texture) == 164, "Ghost shadow ABI changed");
static_assert(offsetof(gc_sample, live_action) == 56, "Ghost action ABI changed");
static_assert(offsetof(gc_sample, pose) == 60, "Ghost pose ABI changed");
static_assert(offsetof(gc_sample, camera) == 204, "Ghost camera ABI changed");
static_assert(offsetof(gc_sample, pending_warp_op) == 240, "Ghost warp ABI changed");
static_assert(offsetof(gc_sample, delayed_warp_timer) == 242, "Ghost countdown ABI changed");
static_assert(offsetof(gc_sample, clock) == 244, "Ghost clock evidence ABI changed");
static_assert(sizeof(float) == 4, "Ghost capture requires IEEE binary32");

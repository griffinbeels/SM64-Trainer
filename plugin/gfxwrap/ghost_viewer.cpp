#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "ghost_viewer.h"
#include "ghost_figure.h"
#include "ghost_playback.h"
#include "ghost_assets.h"
#include "ghost_stream.h"
#include "ghost_annotation_assets.h"
#include "ghost_compositor.h"
#include <atomic>
#include <cstring>
#include <memory>
#include <new>
#include <vector>

namespace {
// The viewer's own model. Exactly one model runtime opens in a module: in the
// viewer's host saved playback never builds one (no trainer asks it), and in
// Project64 setup never runs, so the two never meet.
constexpr uint64_t viewer_model = 0x5649455745520001ull; // "VIEWER", 1
// A loaded recording; immutable once loaded, so a list drawing it keeps its
// own reference while the host replaces the slot.
struct Slot {
    std::vector<uint8_t> data; // the clip as the current GCLIP: header, then rows
    gclip_header header{};
    const gclip_frame *frames = nullptr;
    uint32_t indexed = 0;
    char label[GP_LABEL_BYTES]{};
};
using SlotRef = std::shared_ptr<const Slot>;
// One picture's ghosts as the host published them.
struct Picture {
    uint64_t serial = 0;
    gv_look look{};
    uint32_t count = 0;
    gv_ghost ghosts[GV_GHOSTS]{};
    SlotRef slots[GV_GHOSTS];
};
// What one ghost shows this picture: record is the row its clock reaches (its
// pad and particles), posed the row whose pose is drawn (earlier across a
// bridged refusal); recorded is false on a held end; fade is the end's fade.
struct Choice { bool chosen = false, recorded = true; uint32_t record = 0, posed = 0; float fade = 1; };
// Allocated at setup, so the game's renderer never carries it.
struct Work {
    ghost_pose::Scratch scratch[GV_GHOSTS];
    ghost_compositor::ShadowVertex shadow_vertices[GV_GHOSTS][24];
    uint8_t shadow_texture[GV_GHOSTS][256];
    ghost_compositor::ParticleVertex particles[GV_GHOSTS][ghost_compositor::particle_vertices];
    ghost_annotation::Assets assets;
};

// The host's thread calls the table; the renderer's own thread (GLideN64 runs
// every list on it, while the caller waits) draws. The lock hands a picture's
// ghosts and the status across; the model passes once, detached by setup and
// adopted by the renderer's first call.
SRWLOCK lock = SRWLOCK_INIT;
Picture published;           // under lock
SlotRef loaded[GV_GHOSTS];   // under lock
gv_status last{};            // under lock
gc_layout layout{};          // host thread
sg_model *model = nullptr;   // set once by setup, before model_ready
Work *work = nullptr;
std::atomic<bool> model_ready{false};
// Renderer thread only.
DWORD render_thread = 0;
Picture taken;
struct Plan {
    uint64_t serial = 0, sequence = 0, dlist_serial = 0;
    uint32_t epoch = 0, chosen = 0;
    Choice choice[GV_GHOSTS];
} planned;

struct Locked {
    Locked() { AcquireSRWLockExclusive(&lock); }
    ~Locked() { ReleaseSRWLockExclusive(&lock); }
};
// The renderer's thread, claiming the model on its first call.
bool renderer() {
    if (!model_ready.load(std::memory_order_acquire)) return false;
    const DWORD thread = GetCurrentThreadId();
    if (!render_thread) {
        if (sg_model_adopt(model) != SG_OK) return false;
        render_thread = thread;
    }
    return render_thread == thread;
}

// The row a ghost shows at `frame` game frames after the stage's first, as
// saved playback chooses one at a fixed coordinate (ghost_playback.cpp
// choose): before its start, its first pose; a gap hides it; a refused row
// shows the pose it bridges from, or hides; another level or area hides it.
// Past its last row a star clip's dance tail holds that pose and fades out
// over GP_STAR_FADE_FRAMES, as the race's end does; any other clip is gone.
bool choose(const Slot &slot, int32_t frame, const gc_sample &live, Choice &out) {
    out = {};
    const auto &h = slot.header;
    const uint64_t first = ghost_figure::coordinate(h, slot.frames, 0);
    const uint64_t last_row = ghost_figure::coordinate(h, slot.frames, slot.indexed - 1);
    const uint64_t target = frame < 0 ? first : first + uint64_t(uint32_t(frame));
    uint64_t at = target;
    if (target > last_row) {
        out.recorded = false;
        const uint64_t past = target - last_row;
        if (!(h.flags & GCLIP_DANCE_TAIL) || past >= GP_STAR_FADE_FRAMES) return false;
        out.fade = float(GP_STAR_FADE_FRAMES - past) / float(GP_STAR_FADE_FRAMES);
        at = last_row;
    }
    uint32_t lo = 0, hi = slot.indexed;
    while (lo < hi) {
        const uint32_t middle = lo + (hi - lo) / 2;
        if (ghost_figure::coordinate(h, slot.frames, middle) < at) lo = middle + 1; else hi = middle;
    }
    if (lo >= slot.indexed || ghost_figure::coordinate(h, slot.frames, lo) != at) return false;
    const auto &selected = slot.frames[lo];
    float dx = 0;
    if (!ghost_scene::translation(selected.sample.level, selected.sample.area, live.level, live.area, dx)) return false;
    const gclip_frame *shown = &selected;
    if (!(selected.sample.valid_fields & GC_VALID_POSE)
            && !(shown = ghost_figure::bridged(h, slot.frames, slot.indexed, lo))) return false;
    if (!ghost_scene::compatible(shown->sample, live)) return false;
    out.chosen = true; out.record = lo; out.posed = uint32_t(shown - slot.frames);
    return true;
}
// This list's choice, from the picture the host published last.
void plan_list(const gc_sample &live) {
    {
        Locked held;
        taken = published;
    }
    planned = {};
    planned.serial = taken.serial; planned.epoch = live.epoch;
    planned.sequence = live.sequence; planned.dlist_serial = live.dlist_serial;
    if (live.valid_fields & GC_VALID_CLOCK)
        for (uint32_t i = 0; i < taken.count; ++i)
            if (taken.slots[i] && choose(*taken.slots[i], taken.ghosts[i].frame, live, planned.choice[i]))
                ++planned.chosen;
    Locked held;
    if (last.asked && published.serial == planned.serial) { last.planned = 1; last.chosen = planned.chosen; }
}
bool same_list(const gc_sample &live) {
    return planned.serial == taken.serial && planned.epoch == live.epoch
        && planned.sequence == live.sequence && planned.dlist_serial == live.dlist_serial;
}

int32_t __cdecl setup(const gc_layout *value, const uint8_t *rom, uint32_t bytes) {
    if (!value || !rom || bytes != 8u * 1024u * 1024u) return GV_INVALID;
    if (ghost_capture_validate_layout(value) != GC_OK) return GV_LAYOUT;
    layout = *value;
    if (model_ready.load(std::memory_order_acquire)) return GV_OK;
    if (!work && !(work = new (std::nothrow) Work())) return GV_MEMORY;
    // Built here, handed to the renderer's thread (renderer()) as saved
    // playback's worker hands over its own: open, then detach.
    sg_model *opened = nullptr;
    if (sg_model_open(rom, bytes, &opened) != SG_OK) return GV_MODEL;
    // The names' atlas from this cartridge, as saved playback builds it.
    try { work->assets.load(rom, bytes); } catch (const std::bad_alloc &) { work->assets.ready = false; }
    if (sg_model_detach(opened) != SG_OK) { sg_model_close(&opened); return GV_MODEL; }
    model = opened;
    model_ready.store(true, std::memory_order_release);
    return GV_OK;
}
int32_t __cdecl load(uint32_t index, const uint8_t *clip, uint32_t bytes, const char *label) {
    if (!model_ready.load(std::memory_order_acquire)) return GV_NOT_READY;
    if (index >= GV_GHOSTS) return GV_INVALID;
    if (!bytes) {
        Locked held;
        loaded[index].reset();
        return GV_OK;
    }
    if (!clip || !label) return GV_INVALID;
    char text[GP_LABEL_BYTES]{};
    const size_t length = strnlen(label, GP_LABEL_BYTES);
    if (length >= GP_LABEL_BYTES) return GV_INVALID;
    std::memcpy(text, label, length);
    if (!ghost_annotation::valid_label(text)) return GV_INVALID;
    auto slot = std::make_shared<Slot>();
    try { slot->data.assign(clip, clip + bytes); } catch (const std::bad_alloc &) { return GV_MEMORY; }
    // The worker's own read of a saved clip (ghost_assets::clip: an older
    // version read as current, the header's shape), then playback's own
    // validation of every row (prepare_clip's).
    uint8_t digest[32]{};
    gclip_header header{};
    if (slot->data.size() < sizeof(header)) return GV_CLIP;
    std::memcpy(&header, slot->data.data(), sizeof(header));
    bool memory = false;
    if (!ghost_assets::sha256(slot->data, digest)
            || !ghost_assets::clip(slot->data, digest, header.clip_id, slot->header, &memory))
        return memory ? GV_MEMORY : GV_CLIP;
    slot->frames = reinterpret_cast<const gclip_frame *>(slot->data.data() + sizeof(gclip_header));
    if (!ghost_playback::clip_valid(slot->header, slot->frames, slot->indexed) || !slot->indexed) return GV_CLIP;
    std::memcpy(slot->label, text, sizeof(text));
    Locked held;
    loaded[index] = std::move(slot);
    return GV_OK;
}
int32_t __cdecl picture(const gv_look *value, const gv_ghost *list, uint32_t count) {
    if (!model_ready.load(std::memory_order_acquire)) return count ? GV_NOT_READY : GV_OK;
    if (count > GV_GHOSTS || (count && (!value || !list))) return GV_INVALID;
    if (count && (!(value->opacity >= 0 && value->opacity <= 1) || !(value->name_background_opacity >= 0
            && value->name_background_opacity <= 1) || (value->flags & ~63u) || value->reserved)) return GV_INVALID;
    {
        Locked held;
        uint32_t seen = 0;
        for (uint32_t i = 0; i < count; ++i) {
            const auto &ghost = list[i];
            if (ghost.slot >= GV_GHOSTS || (seen & (1u << ghost.slot)) || !loaded[ghost.slot]
                    || ghost.hue_degrees > 359 || ghost.reserved) return GV_INVALID;
            seen |= 1u << ghost.slot;
        }
        Picture next;
        next.serial = published.serial + 1; next.count = count;
        if (count) {
            next.look = *value;
            for (uint32_t i = 0; i < count; ++i) { next.ghosts[i] = list[i]; next.slots[i] = loaded[list[i].slot]; }
        }
        published = std::move(next);
        last = {}; last.bytes = sizeof(last); last.asked = count;
    }
    // Every picture with ghosts reads the stage afresh: a new capture
    // configuration is a new epoch, so a frame drawn after a later one (a seek,
    // a step back) is never refused as a clock that ran backward. A picture
    // without ghosts leaves the capture off, as a host without ghosts does.
    if (count) return ghost_stream::api()->configure(&layout) ? GV_OK : GV_LAYOUT;
    ghost_stream::api()->disable();
    return GV_OK;
}
int32_t __cdecl status(gv_status *out, uint32_t bytes) {
    if (!out || bytes != sizeof(*out)) return GV_INVALID;
    Locked held;
    *out = last; out->bytes = sizeof(*out);
    return GV_OK;
}
const gv_api table = {sizeof(gv_api), GV_VERSION, {0, 0}, setup, load, picture, status};
}

namespace ghost_viewer {
const gv_api *api() { return &table; }
bool atlas(sg_atlas_view *out, uint64_t *id) {
    if (out) *out = {};
    if (id) *id = 0;
    if (!out || !id || !renderer() || sg_model_get_atlas(model, out) != SG_OK) return false;
    *id = viewer_model; return true;
}
bool particles(sg_particle_view *out, uint64_t *id) {
    if (out) *out = {};
    if (id) *id = 0;
    if (!out || !id || !renderer() || sg_model_get_particles(model, out) != SG_OK || !out->frame_count) return false;
    *id = viewer_model; return true;
}
bool annotations(ghost_annotation::View *out, uint64_t *id) {
    if (out) *out = {};
    if (id) *id = 0;
    if (!out || !id || !renderer()) return false;
    *out = work->assets.view();
    if (!out->rgba) return false;
    *id = viewer_model; return true;
}
bool plan(const gc_sample &live) {
    if (!renderer()) return false;
    plan_list(live);
    return planned.chosen != 0;
}
bool evaluate(const gc_sample &live, const ghost::WorldPass &world, ghost_compositor::Pose *out, uint32_t capacity,
              uint32_t &count) {
    count = 0;
    if (!out || !renderer()) return false;
    if (!same_list(live)) plan_list(live);
    if (!taken.count) return false;
    const auto &look = taken.look;
    ghost_figure::Look figure{};
    figure.opacity = look.opacity; figure.shadow = (look.flags & GV_SHADOW) != 0;
    ghost_figure::Names names{};
    names.names = (look.flags & GV_NAMES) != 0; names.inputs = (look.flags & GV_INPUTS) != 0;
    names.values = (look.flags & GV_VALUES) != 0;
    names.background = uint8_t((look.flags & GV_WHITE_NAME) ? GP_NAME_WHITE : GP_NAME_BLACK);
    names.background_opacity = look.name_background_opacity;
    for (uint32_t i = 0; i < taken.count && count < capacity; ++i) {
        const auto &choice = planned.choice[i];
        if (!choice.chosen) continue;
        const Slot &slot = *taken.slots[i];
        const auto &shown = slot.frames[choice.posed];
        gs_record recorded{};
        recorded.bytes = sizeof(recorded); recorded.version = GS_VERSION;
        recorded.configuration = shown.configuration; recorded.sample = shown.sample;
        recorded.world.status = shown.world_status;
        figure.hue_degrees = (look.flags & GV_TINT) ? float(taken.ghosts[i].hue_degrees) : 0.0f;
        ghost_figure::Storage storage{work->scratch[i], work->shadow_vertices[i], work->shadow_texture[i],
                                      work->particles[i]};
        float position[3]{};
        ghost_compositor::Pose pose{};
        if (ghost_figure::draw(model, slot.header.rom_layout, recorded, slot.frames[choice.record].sample, live, world,
                               figure, choice.fade, storage, position, pose) != ghost_figure::Drawn::ready
                || !pose.count) continue;
        ghost_figure::annotate(slot.label, position, shown.sample.pose.scale[1], uint8_t(i), names, choice.recorded,
                               slot.frames[choice.record].sample, work->assets.usamune, work->assets.font, pose);
        out[count++] = pose;
    }
    Locked held;
    if (published.serial == taken.serial) last.posed = count;
    return count != 0;
}
bool current() {
    if (!renderer() || !taken.count) return false;
    Locked held;
    return published.serial == taken.serial;
}
void finished(bool displayed) {
    if (!renderer()) return;
    Locked held;
    if (published.serial == taken.serial) last.displayed = displayed ? 1u : 0u;
}
}

#pragma comment(linker,"/EXPORT:SM64ViewerGhostsV1=_SM64ViewerGhostsV1")
extern "C" const gv_api *__cdecl SM64ViewerGhostsV1(uint32_t version, uint32_t bytes) {
    return version == GV_VERSION && bytes == sizeof(gv_api) ? ghost_viewer::api() : nullptr;
}

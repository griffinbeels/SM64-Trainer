/* What the ghost draw path did, as plain facts (2026-10-30). An AMD tester's
 * reports showed playback READY, three clips prepared and no ghost on screen:
 * every early exit of the draw path was silent. The render thread records the
 * first step that refused the most recent display list and the most recent
 * picture, plus the renderer settings those steps read, using only values it
 * already holds: no GL call and no driver query. The IPC worker copies them
 * into the ghost page (gipc_page.draw, ghost_ipc.h) under its mutex; Python
 * decodes them in ghost/draw_facts.py.
 *
 * Zero is "this renderer did not say", never "fine": every gate's 0 is
 * unknown, and a group of facts counts only while its GDF_KNOWN_* bit is set.
 * Every list below is append-only: Python reads codes by position. */
#pragma once
#include <stdint.h>

#define GDF_VERSION 1u

/* The first step that refused the most recent display list (gdf_facts.list_gate),
 * in the order the list runs: ghost_link's list start, ghost_draw's begin,
 * its seal and its finish. DREW: no step refused, the list's ghost image
 * was finished for the picture. */
#define GDF_LIST_GATES(X) \
    X(UNKNOWN) X(DREW) X(NO_CONTEXT) X(NOT_REQUESTED) X(BINDINGS) X(RDRAM_SIZE) X(NOT_OBSERVED) \
    X(OTHER_THREAD) X(NO_CLEAN_CONSUMER) X(NO_IDENTITY) X(NO_DISPATCH) X(CREATE_SCOPE) X(NO_COMPOSITOR) \
    X(FAULTED) X(NO_MODEL) X(NO_EXTENT) X(PREPARE_SCOPE) X(PREPARE) X(NO_SAMPLE) X(NO_PLAN) X(NO_SEAL) \
    X(SEAL_WORLD) X(SEAL_BUFFER) X(NO_POSE) X(SEAL_SCOPE) X(BATCH) X(TARGET_CHANGED) X(WORLD_LOST) \
    X(HUD) X(STALE) X(CLOSE_DIRTY) X(FINALIZE)
/* The first step that refused the most recent picture (gdf_facts.present_gate):
 * ghost_presentation's VI draw, then replace_vi. SHOWN: the ghost image
 * replaced the picture (the swap's receipt is still status.displayed). */
#define GDF_PRESENT_GATES(X) \
    X(UNKNOWN) X(SHOWN) X(NO_CLEAN_CONSUMER) X(NO_PRESENTATION) X(NO_PROVIDER) X(IDENTITY) X(SPLIT) \
    X(OVERSCAN) X(MULTISAMPLING) X(FXAA) X(BLIT_WORKAROUND) X(SOURCE) X(SOURCE_EXTENT) X(NO_IMAGE) \
    X(NO_DISPLAY_BUFFER) X(EXTENT) X(NOT_READY) X(REPEAT) X(FAULTED) X(IMAGE) X(SRGB) X(COPY) X(DRAW)
/* How ghost_presentation::context_ready last ended (gdf_facts.ready_step). */
#define GDF_READY_STEPS(X) \
    X(UNKNOWN) X(READY) X(NO_CLEAN_CONSUMER) X(NO_IDENTITY) X(SOURCE_FORMAT) X(DISPATCH) X(SCOPE) \
    X(CREATE) X(GAMMA) X(DIRTY) X(EXTENT)
/* How ghost_draw's compositor creation last ended for its context (gdf_facts.create_step). */
#define GDF_CREATE_STEPS(X) X(UNKNOWN) X(CREATED) X(DISPATCH) X(SCOPE) X(REFUSED) X(DIRTY)
/* Which ghost_draw scope closed with a GL error and made the ghost step aside
 * for its context (gdf_facts.fault_step; meaningful while GDF_FAULTED is set). */
#define GDF_FAULT_STEPS(X) X(NONE) X(CREATE) X(PREPARE) X(PARTICLES) X(ANNOTATIONS) X(DISPLAY)

#define GDF_ENUM_LIST(name) GDF_LIST_##name,
#define GDF_ENUM_PRESENT(name) GDF_PRESENT_##name,
#define GDF_ENUM_READY(name) GDF_READY_##name,
#define GDF_ENUM_CREATE(name) GDF_CREATE_##name,
#define GDF_ENUM_FAULT(name) GDF_FAULT_##name,
enum gdf_list_gate { GDF_LIST_GATES(GDF_ENUM_LIST) GDF_LIST_GATE_COUNT };
enum gdf_present_gate { GDF_PRESENT_GATES(GDF_ENUM_PRESENT) GDF_PRESENT_GATE_COUNT };
enum gdf_ready_step { GDF_READY_STEPS(GDF_ENUM_READY) GDF_READY_STEP_COUNT };
enum gdf_create_step { GDF_CREATE_STEPS(GDF_ENUM_CREATE) GDF_CREATE_STEP_COUNT };
enum gdf_fault_step { GDF_FAULT_STEPS(GDF_ENUM_FAULT) GDF_FAULT_STEP_COUNT };
#undef GDF_ENUM_LIST
#undef GDF_ENUM_PRESENT
#undef GDF_ENUM_READY
#undef GDF_ENUM_CREATE
#undef GDF_ENUM_FAULT

/* gdf_facts.known: which groups the renderer has recorded. */
#define GDF_KNOWN_LIST 1u     /* a list ended: list_gate, the draw flags, results and extents */
#define GDF_KNOWN_SETTINGS 2u /* a list started: settings, multisampling, rdram_size */
#define GDF_KNOWN_PRESENT 4u  /* a picture was offered: present_gate, presents */
#define GDF_KNOWN_PLAYBACK 8u /* the worker read playback's counters */
#define GDF_KNOWN_SWAP 16u    /* a swap completed: the presentation flags */
/* The step enums need no bit: each one's 0 is UNKNOWN. */
/* gdf_facts.flags */
#define GDF_OBSERVING 1u            /* the pose stream observed the most recent list */
#define GDF_FAULTED 2u              /* ghost_draw stepped aside for the current context */
#define GDF_COMPOSITOR 4u           /* a compositor exists for the current context */
#define GDF_MODEL 8u                /* the compositor holds Mario's prepared model */
#define GDF_PLANNED 16u             /* playback planned a ghost for the most recent list */
#define GDF_PRESENTATION 32u        /* the presentation surfaces are ready for the context */
#define GDF_PRESENTATION_FAULTED 64u /* the presentation stepped aside after a GL error */
/* gdf_facts.settings: GLideN64's own settings, as the list read them. */
#define GDF_SETTING_FBE 1u
#define GDF_SETTING_FXAA 2u
#define GDF_SETTING_OVERSCAN 4u
#define GDF_SETTING_BLIT_WORKAROUND 8u
#define GDF_SETTING_N64_DEPTH_COMPARE 16u
#define GDF_SETTING_FRAGMENT_DEPTH_WRITE 32u

#pragma pack(push, 4)
typedef struct gdf_facts {
    uint32_t version;    /* GDF_VERSION; 0: the renderer reports none */
    uint32_t generation; /* the IPC status generation this copy was written for */
    uint32_t known, flags;
    uint32_t list_gate, lists;       /* gdf_list_gate; lists ended so far (wraps) */
    uint32_t present_gate, presents; /* gdf_present_gate; pictures offered so far (wraps) */
    uint32_t ready_step, create_step;
    /* ghost_compositor::Result + 1 of the latest call (0: never called this context) */
    uint32_t prepare_result, batch_result, finalize_result;
    /* The scope that faulted, and the renderer's record of the GL error behind
     * it (replay_gl::BindingsCause: its site and error, read once at the fault). */
    uint32_t fault_step, fault_site, fault_error;
    uint32_t observed_width, observed_height, prepared_width, prepared_height;
    uint32_t settings, multisampling;
    uint32_t rdram_size; /* GLideN64's RDRAMSize: the largest byte index, 0x7fffff for 8 MiB */
    uint32_t evaluations, playback_refusals; /* gp_status counters */
    uint32_t reserved[7];
} gdf_facts;
#pragma pack(pop)

#ifdef __cplusplus
#include <atomic>
static_assert(sizeof(gdf_facts) == 128, "ghost draw facts bytes");
namespace ghost_facts {
/* Render thread only. The list's and the picture's first refusal, kept until
 * that list or picture ends; then one relaxed store each publishes them. */
inline uint32_t list_pending = 0, present_pending = 0, list_count = 0, present_count = 0;
inline bool list_observing = false;
inline void refuse(uint32_t gate) { if (!list_pending) list_pending = gate; }
inline void present_refuse(uint32_t gate) { if (!present_pending) present_pending = gate; }
/* What the worker copies; each is a value the render thread already held. */
inline std::atomic<uint32_t> list_gate{0}, lists{0}, list_flags{0}, present_gate{0}, presents{0}, present_flags{0};
inline std::atomic<uint32_t> ready_step{0}, create_step{0}, prepare_result{0}, batch_result{0}, finalize_result{0};
inline std::atomic<uint32_t> fault_step{0}, fault_site{0}, fault_error{0};
inline std::atomic<uint32_t> observed_width{0}, observed_height{0}, prepared_width{0}, prepared_height{0};
inline std::atomic<uint32_t> settings{0}, multisampling{0}, rdram_size{0}, settings_known{0};
inline std::atomic<uint32_t> present_known{0}, swap_known{0};
constexpr auto relaxed = std::memory_order_relaxed;
// The list's end: DREW when its image was finished, else its first refusal
// (a list that recorded none ended before anything sealed).
inline void end_list(bool drew, uint32_t flags) {
    list_gate.store(drew ? uint32_t(GDF_LIST_DREW) : list_pending ? list_pending : uint32_t(GDF_LIST_NO_SEAL), relaxed);
    list_flags.store(flags | (list_observing ? GDF_OBSERVING : 0u), relaxed);
    lists.store(++list_count, relaxed);
    list_pending = 0;
}
// The picture's end: SHOWN when nothing refused it.
inline void end_present() {
    present_gate.store(present_pending ? present_pending : uint32_t(GDF_PRESENT_SHOWN), relaxed);
    presents.store(++present_count, relaxed); present_known.store(1, relaxed);
    present_pending = 0;
}
// Publishes the picture's facts however its admission returns.
struct Present {
    Present() { present_pending = 0; }
    ~Present() { end_present(); }
    void refuse(uint32_t gate) { present_refuse(gate); }
};
inline void presentation(bool ready, bool faulted) {
    present_flags.store((ready ? GDF_PRESENTATION : 0u) | (faulted ? GDF_PRESENTATION_FAULTED : 0u), relaxed);
    swap_known.store(1, relaxed);
}
inline void ready(uint32_t step) { ready_step.store(step, relaxed); }
inline void renderer(uint32_t setting_bits, uint32_t samples, uint32_t rdram) {
    settings.store(setting_bits, relaxed); multisampling.store(samples, relaxed);
    rdram_size.store(rdram, relaxed); settings_known.store(1, relaxed);
}
// The worker's copy (any thread). Groups the render thread never wrote stay unknown.
inline gdf_facts snapshot() {
    gdf_facts out{};
    out.version = GDF_VERSION;
    const uint32_t ended = lists.load(relaxed);
    out.known = (ended ? GDF_KNOWN_LIST : 0u) | (settings_known.load(relaxed) ? GDF_KNOWN_SETTINGS : 0u)
        | (present_known.load(relaxed) ? GDF_KNOWN_PRESENT : 0u) | (swap_known.load(relaxed) ? GDF_KNOWN_SWAP : 0u);
    out.flags = list_flags.load(relaxed) | present_flags.load(relaxed);
    out.list_gate = list_gate.load(relaxed); out.lists = ended;
    out.present_gate = present_gate.load(relaxed); out.presents = presents.load(relaxed);
    out.ready_step = ready_step.load(relaxed); out.create_step = create_step.load(relaxed);
    out.prepare_result = prepare_result.load(relaxed); out.batch_result = batch_result.load(relaxed);
    out.finalize_result = finalize_result.load(relaxed);
    out.fault_step = fault_step.load(relaxed); out.fault_site = fault_site.load(relaxed);
    out.fault_error = fault_error.load(relaxed);
    out.observed_width = observed_width.load(relaxed); out.observed_height = observed_height.load(relaxed);
    out.prepared_width = prepared_width.load(relaxed); out.prepared_height = prepared_height.load(relaxed);
    out.settings = settings.load(relaxed); out.multisampling = multisampling.load(relaxed);
    out.rdram_size = rdram_size.load(relaxed);
    return out;
}
}
#endif

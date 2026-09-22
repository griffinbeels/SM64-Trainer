/* Exact target-owner markers, resolved off the render thread. UTC narrows a
 * source identity; it never selects a nearby game frame. */
#pragma once
#include <stdint.h>

enum go_kind { GO_NONE, GO_STAR, GO_SEGMENT };
enum go_result { GO_READY, GO_MISSING, GO_AMBIGUOUS, GO_INVALID };
enum go_flags { GO_RECOVERED = 1u };
#define GO_HISTORY 4096u
#define GO_UTC_WINDOW 1.0
#pragma pack(push, 4)
typedef struct go_marker {
    uint32_t kind, configuration, epoch, global_timer;
    double utc;
    uint64_t session_id, arm_id;
} go_marker;
typedef struct go_origin {
    uint32_t kind, epoch;
    uint64_t sequence, continuity, elapsed, star_generation;
    /* Coordinate at this elapsed reference: raw IGT for a normal star,
     * zero for a normal segment, witnessed LIVE coordinate after a restore. */
    uint32_t offset, flags;
} go_origin;
#pragma pack(pop)

#ifdef __cplusplus
#include <vector>
static_assert(sizeof(go_marker) == 40, "ghost target marker ABI");
static_assert(sizeof(go_origin) == 48, "ghost resolved origin ABI");
struct gc_sample;
namespace ghost_origin {
/* Worker-owned, bounded, no renderer access or retained source pointers.
 * Namespace changes discard history rather than splice two source sessions. */
class Ledger {
public:
    void clear();
    void add(uint32_t configuration, const gc_sample &sample);
    go_result resolve(const go_marker &, uint64_t qpc_frequency,
                      int64_t qpc_anchor, double utc_anchor,
                      uint32_t near_zero, go_origin &) const;
private:
    struct Entry {
        uint32_t timer, raw, valid, clock_valid;
        int64_t qpc;
        uint64_t sequence, continuity, elapsed, star_generation;
    } entries_[GO_HISTORY]{};
    uint32_t configuration_ = 0, epoch_ = 0, count_ = 0, next_ = 0;
    uint64_t sequence_ = 0;
};
/* Presentation-only checkpoints. Source epochs/clocks remain immutable.
 * Worker-owned storage and bounded lookup; never called by the renderer. */
class Recovery {
public:
    void clear();
    void bind(const go_origin &);
    void suspend();
    /* Capacity for the whole checkpoint history, off the render thread;
     * afterwards clear/bind/observe keep it and never allocate. */
    void reserve() { history_.reserve(limit); }
    bool bound() const { return origin_.sequence != 0; }
    bool current(go_origin &) const;
    bool restored_near(const go_marker &, uint64_t frequency, int64_t anchor, double utc) const;
    go_result observe(const gc_sample &, const uint32_t *star_actions, uint32_t count);
private:
    struct Checkpoint {
        uint32_t epoch=0,timer=0,raw=0,fields=0;
        int16_t level=0,area=0;
        uint64_t sequence=0,continuity=0,elapsed=0,star=0;
        int64_t qpc=0;
        float position[3]{};
        int16_t angles[3]{},animation=0,animation_frame=0;
        uint32_t action=0,coordinate=0;
    } latest_{}, restoration_{};
    static constexpr uint32_t limit = 16384;
    std::vector<Checkpoint> history_;
    uint32_t next_=0;
    go_origin origin_{};
    bool pending_=false,ended_=false,absolute_timer_=false,direct_star_=false;
    int16_t first_level_=0,first_area_=0;
    bool coordinate(const Checkpoint &, uint32_t &) const;
    void remember(const Checkpoint &);
};
}
#endif

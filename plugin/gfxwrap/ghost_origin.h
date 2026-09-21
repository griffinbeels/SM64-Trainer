/* Exact target-owner markers, resolved off the render thread. UTC narrows a
 * source identity; it never selects a nearby game frame. */
#pragma once
#include <stdint.h>

enum go_kind { GO_NONE, GO_STAR, GO_SEGMENT };
enum go_result { GO_READY, GO_MISSING, GO_AMBIGUOUS, GO_INVALID };
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
    uint32_t raw, reserved;
} go_origin;
#pragma pack(pop)

#ifdef __cplusplus
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
}
#endif

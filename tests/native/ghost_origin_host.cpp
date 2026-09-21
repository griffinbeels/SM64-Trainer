#include "ghost_origin.h"
#include "ghost_capture.h"
#include <cstdio>
#include <stdexcept>

namespace {
unsigned checks = 0;
void check(bool value, const char *why) { ++checks; if (!value) throw std::runtime_error(why); }
gc_sample sample(uint64_t sequence, uint32_t timer, uint64_t elapsed = 42) {
    gc_sample result{};
    result.bytes = sizeof(result); result.version = GC_VERSION;
    result.epoch = 7; result.sequence = sequence; result.global_timer = timer;
    result.valid_fields = GC_VALID_CLOCK; result.igt = 1;
    result.qpc = 1000; result.clock.valid = GCC_RAW_VALID | GCC_ELAPSED_VALID;
    result.clock.continuity_id = 9; result.clock.elapsed_frames = elapsed;
    return result;
}
go_marker marker() { return {GO_STAR, 2, 7, 100, 100.0, 1, 2}; }
go_result resolve(const ghost_origin::Ledger &ledger, const go_marker &mark, go_origin &out) {
    return ledger.resolve(mark, 1000, 1000, 100.0, 30, out);
}
}
int main() {
    try {
        ghost_origin::Ledger ledger; go_origin out{}; auto mark = marker();
        check(resolve(ledger, mark, out) == GO_MISSING, "empty ledger cannot invent origin");
        ledger.add(2, sample(1, 100)); ledger.add(2, sample(2, 100));
        check(resolve(ledger, mark, out) == GO_READY && out.sequence == 1 && out.elapsed == 42
            && out.raw == 1 && out.star_generation == 0 && out.continuity == 9,
            "identical coordinate duplicates bind first even with absent pose and unknown star bank");
        mark.global_timer = 99;
        check(resolve(ledger, mark, out) == GO_MISSING && !out.sequence, "nearby frame is not exact");
        mark = marker(); mark.utc = 102;
        check(resolve(ledger, mark, out) == GO_MISSING, "UTC excludes stale occurrence");
        mark = marker(); mark.epoch = 8;
        check(resolve(ledger, mark, out) == GO_MISSING, "foreign epoch refused");
        mark = marker(); mark.arm_id = 0;
        check(resolve(ledger, mark, out) == GO_INVALID, "missing owner identity refused");
        mark = marker(); ledger.add(2, sample(3, 100, 43));
        check(resolve(ledger, mark, out) == GO_AMBIGUOUS, "same frame with distinct coordinate refused");
        ledger.clear(); auto invalid = sample(1, 100); invalid.clock = {};
        ledger.add(2, invalid);
        check(resolve(ledger, mark, out) == GO_INVALID, "first timing refusal retained");
        ledger.add(2, sample(2, 100));
        check(resolve(ledger, mark, out) == GO_AMBIGUOUS, "later clock cannot replace refused first occurrence");
        ledger.clear(); invalid = sample(1, 100); invalid.valid_fields = 0;
        ledger.add(2, invalid);
        check(resolve(ledger, mark, out) == GO_INVALID, "missing raw clock remains a refusal");
        ledger.add(2, sample(2, 100));
        check(resolve(ledger, mark, out) == GO_AMBIGUOUS, "missing first raw clock cannot be skipped");
        ledger.clear(); invalid = sample(1, 100); invalid.clock.valid = GCC_ELAPSED_VALID;
        ledger.add(2, invalid);
        check(resolve(ledger, mark, out) == GO_INVALID, "derived elapsed alone cannot claim valid raw clock");
        ledger.clear(); auto late = sample(1, 100); late.igt = 80; ledger.add(2, late);
        check(resolve(ledger, mark, out) == GO_INVALID, "mid-leg raw counter cannot claim star opener");
        mark.kind = GO_SEGMENT;
        check(resolve(ledger, mark, out) == GO_READY && out.raw == 80, "segment can start mid-leg");
        ledger.clear(); mark = marker(); ledger.add(2, sample(1, 100));
        for (uint32_t i = 0; i < GO_HISTORY; ++i) ledger.add(2, sample(i + 2, i + 101, i + 43));
        check(resolve(ledger, mark, out) == GO_MISSING, "bounded history expires old exact origin");
        ledger.clear(); ledger.add(2, sample(5, 100));
        auto stale = sample(4, 100); stale.epoch = 6; ledger.add(2, stale);
        check(resolve(ledger, mark, out) == GO_READY && out.sequence == 5, "stale old epoch cannot replace history");
        auto next = sample(6, 100); next.epoch = 8; ledger.add(2, next);
        check(resolve(ledger, mark, out) == GO_MISSING, "new capture epoch invalidates old origin");
        std::printf("ghost origin: %u checks passed\n", checks); return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "ghost origin: %s (check %u)\n", error.what(), checks); return 1;
    }
}

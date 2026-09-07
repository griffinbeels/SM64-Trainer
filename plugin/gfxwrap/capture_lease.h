/* A requested capture expires unless its reader continues to renew it.
 * Pure state machine so crash/clock-wrap cases can be tested without a GPU.
 * GetTickCount subtraction is wrap-safe for this three-second interval. */
#pragma once
#include <stdint.h>
#define CAPTURE_LEASE_MS 3000u
typedef struct {
    uint32_t counter, renewed_at;
    int observed;
} capture_lease_t;

static int capture_lease_active(capture_lease_t *lease, uint32_t counter,
                                uint32_t want_frames, uint32_t now) {
    if (counter != lease->counter) {
        lease->counter = counter;
        lease->renewed_at = now;
        lease->observed = 1;
    }
    return want_frames && lease->observed
        && (uint32_t)(now - lease->renewed_at) < CAPTURE_LEASE_MS;
}

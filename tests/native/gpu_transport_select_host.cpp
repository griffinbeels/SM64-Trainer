/* CPU-only: the shipped bridge transport selection rule (gpu_render.cpp)
 * over capability sets, with no GL context. Prints one line per case:
 * "<caps> <forced> <floor> -> <transport> <unavailable reason>". */
#include "gpu_render.h"
#include <cstdio>

int main() {
    const uint32_t base = GT_CAP_PROBED | GT_CAP_GL45 | GT_CAP_MEMORY_OBJECT | GT_CAP_MEMORY_OBJECT_WIN32;
    const uint32_t semaphore = GT_CAP_SEMAPHORE | GT_CAP_SEMAPHORE_WIN32;
    const struct { uint32_t caps, forced, floor; } cases[] = {
        {base | GT_CAP_KEYED_MUTEX | semaphore, 0, 1},        // NVIDIA: the keyed mutex
        {base | semaphore, 0, 1},                             // the AMD field report: no keyed mutex
        {base | GT_CAP_SEMAPHORE, 0, 1},                      // semaphore without its win32 import
        {base, 0, 1},                                         // neither lock: readback
        {base | GT_CAP_KEYED_MUTEX | semaphore, 0, 2},        // keyed below the floor
        {base | semaphore, 0, 3},                             // semaphore refused earlier
        {base | semaphore, 0, 4},                             // every transport refused
        {base | GT_CAP_KEYED_MUTEX | semaphore, 3, 1},        // a test host forcing readback
        {base | GT_CAP_KEYED_MUTEX, 2, 1},                    // forcing a transport the context lacks
        {GT_CAP_PROBED | GT_CAP_MEMORY_OBJECT | GT_CAP_MEMORY_OBJECT_WIN32, 0, 1}, // no GL 4.5
        {GT_CAP_PROBED | GT_CAP_GL45 | GT_CAP_MEMORY_OBJECT_WIN32, 0, 1},         // no memory objects
        {GT_CAP_PROBED | GT_CAP_GL45 | GT_CAP_MEMORY_OBJECT, 0, 1},               // no win32 memory (no LUID)
        {base & ~GT_CAP_PROBED, 0, 1},                        // never probed
    };
    for (const auto &c : cases)
        printf("%08x %u %u -> %u %u\n", c.caps, c.forced, c.floor,
               render_capture::select_transport(c.caps, c.forced, c.floor),
               unsigned(render_capture::unavailable(c.caps)));
    return 0;
}

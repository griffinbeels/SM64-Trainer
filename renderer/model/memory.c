#include "internal.h"
#include "decomp/memory.h"
#include <string.h>

/* All upstream graph allocations occur inside a C-only operation guarded by
 * setjmp in model.c. Exhaustion unwinds to that guard before callers can write
 * through NULL. No C++ stack frames/destructors are crossed. Each operation
 * resets its scratch arena; initialization owns the permanent arena.
 */
struct AllocOnlyPool *alloc_only_pool_init(void)
{
    unsigned i;
    for (i = 0; i < 8; ++i) {
        if (!sg_active->pools[i].active) {
            sg_active->pools[i].active = 1;
            sg_active->pools[i].permanent = sg_active->building;
            return &sg_active->pools[i];
        }
    }
    sg_abort(SG_CAPACITY);
    return NULL;
}

void *alloc_only_pool_alloc(struct AllocOnlyPool *pool, s32 size)
{
    size_t amount, used, limit;
    uint8_t *base;
    if (!pool || !pool->active || size < 0)
        sg_abort(SG_CAPACITY);
    amount = ((size_t)size + 15u) & ~(size_t)15u;
    used = pool->permanent ? sg_active->permanent_used : sg_active->scratch_used;
    limit = pool->permanent ? SG_PERMANENT_BYTES : sg_active->scratch_limit;
    base = pool->permanent ? sg_active->permanent : sg_active->scratch;
    if (used > limit || amount > limit - used)
        sg_abort(SG_CAPACITY);
    memset(base + used, 0, amount);
    if (pool->permanent) sg_active->permanent_used += amount;
    else sg_active->scratch_used += amount;
    return base + used;
}

void alloc_only_pool_free(struct AllocOnlyPool *pool)
{
    if (pool) pool->active = 0;
}

void memory_init(void) { sg_active->display_pool = alloc_only_pool_init(); }
void memory_terminate(void) { sg_active->display_pool = NULL; }

void sg_memory_reset(void)
{
    unsigned i;
    sg_active->scratch_used = 0;
    for (i = 0; i < 8; ++i)
        if (!sg_active->pools[i].permanent) sg_active->pools[i].active = 0;
    sg_active->display_pool = NULL;
}

void display_list_pool_reset(void)
{
    sg_memory_reset();
    sg_active->display_pool = alloc_only_pool_init();
}

void *alloc_display_list(u32 size)
{
    if (size > INT32_MAX) sg_abort(SG_CAPACITY);
    return alloc_only_pool_alloc(sg_active->display_pool, (s32)size);
}

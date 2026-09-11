/* Compile the whole production wrapper against observable CPU substitutes.
 * These counts measure calls actually reached, not source-code spellings. */
#include <windows.h>
#include <GL/gl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
static unsigned context_calls, ram_copies, query_calls, list_calls, update_calls, screen_calls;
static void *forwarded_list;
static unsigned forwarded_size;
static uint32_t mock_now;
static unsigned char ram[128];
static HGLRC WINAPI counted_context(void) { context_calls++; return NULL; }
static DWORD WINAPI controlled_tick(void) { return mock_now; }
static SIZE_T WINAPI counted_query(LPCVOID p, PMEMORY_BASIC_INFORMATION info, SIZE_T n) {
    query_calls++; return VirtualQuery(p, info, n);
}
static void *counted_copy(void *to, const void *from, size_t bytes) {
    if ((uintptr_t)from >= (uintptr_t)ram && (uintptr_t)from < (uintptr_t)ram + sizeof ram)
        ram_copies++;
    return memcpy(to, from, bytes);
}
#define wglGetCurrentContext counted_context
#define GetTickCount controlled_tick
#define VirtualQuery counted_query
#define memcpy counted_copy
#include "gfxwrap.c"
#undef memcpy
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "passthrough contract line %d\n", __LINE__); return 1; } } while (0)

static void fake_list(void) { list_calls++; }
static void fake_update(void) { update_calls++; }
static void fake_batch(void *entries, unsigned size) { forwarded_list = entries; forwarded_size = size; }
static void fake_screen(void **out, long *width, long *height) {
    screen_calls++;
    *out = HeapAlloc(GetProcessHeap(), 0, 12);
    memset(*out, 0x73, 12);
    *width = 2; *height = 2;
}

int main(void) {
    void *view = VirtualAlloc(NULL, TOTAL_BYTES, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    CHECK(view != NULL);
    g_hdr = view;
    g_slots = (uint8_t *)view + HEADER_BYTES;
    g_have_gfx = TRUE;
    g_wrapped.ProcessDList = fake_list;
    g_wrapped.UpdateScreen = fake_update;
    g_wrapped.ReadScreen = fake_screen;
    g_wrapped.FBWList = fake_batch;
    FBWList(ram, 19);
    CHECK(forwarded_list == ram && forwarded_size == 19);
    g_gfx.RDRAM = ram;
    g_rdram_span = sizeof ram;
    unsigned origin = 0;
    g_gfx.VI_ORIGIN_REG = &origin;
    g_hdr->table_count = 1;
    g_hdr->table[0].length = 4;
    g_hdr->rdram_bytes = sizeof ram;
    for (unsigned n = 0; n < 100; n++) {
        origin++;
        ProcessDList(); UpdateScreen();
    }
    CHECK(list_calls == 100 && update_calls == 100);
    CHECK(g_hdr->lists == 100 && g_hdr->alive == 100);
    CHECK(context_calls == 0 && ram_copies == 0 && query_calls == 0 && screen_calls == 0);
    CHECK(g_hdr->write_seq == 0 && g_pending.lists_since == 0);

    /* Live demand restores identical stamps and bytes. */
    g_hdr->want_frames = 1; g_hdr->tracker_alive = 1;
    ram[0] = 41;
    ProcessDList(); origin++; UpdateScreen();
    CHECK(ram_copies == 1 && screen_calls == 1 && g_hdr->write_seq == 1);
    CHECK(slot_at(0)->table[0] == 41 && slot_at(0)->lists_since == 1);
    CHECK(slot_at(0)->pixels[0] == 0x73);

    /* A killed reader retains want=1; expiry stops all capture work. */
    mock_now = CAPTURE_LEASE_MS;
    unsigned old_contexts = context_calls, old_copies = ram_copies;
    ProcessDList(); origin++; UpdateScreen();
    CHECK(context_calls == old_contexts && ram_copies == old_copies && screen_calls == 1);
    CHECK(g_pending.count == 0 && g_pending.lists_since == 0);

    /* Reader resumes after this display list: its first picture is explicitly
     * unstamped, never mislabeled using the pre-expiry frame. */
    g_hdr->tracker_alive++;
    ram[0] = 99;
    origin++; UpdateScreen();
    CHECK(g_hdr->write_seq == 2 && slot_at(1)->table_count == 0 && slot_at(1)->lists_since == 0);
    ProcessDList(); origin++; UpdateScreen();
    CHECK(g_hdr->write_seq == 3 && slot_at(2)->table[0] == 99 && slot_at(2)->lists_since == 1);
    g_hdr = NULL; g_slots = NULL;
    VirtualFree(view, 0, MEM_RELEASE);
    return 0;
}

/* The renderer-side capture boundary behind SourceV2. link_source_api.h pins
 * these record/surface layouts as the SourceV2 ABI. */
#pragma once
#include <windows.h>
#include <stdint.h>
#include "source_format.h"

#ifdef RB_BUILD_DLL
#define RB_API __declspec(dllexport)
#else
#define RB_API
#endif

#define RB_SLOTS 8u
#define RB_STAMP_BYTES 2048u
#define RB_STAMP_FIELDS 16u

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int64_t list_qpc;
    uint32_t vi_origin, lists_since, table_count;
    uint32_t lengths[RB_STAMP_FIELDS];
    uint8_t bytes[RB_STAMP_BYTES];
} rb_stamp;

typedef struct {
    uint32_t width, height, bottom_offset, renderer_thread;
    uintptr_t context;
    int64_t boundary_qpc;
    /* Test/diagnostic witness only; never a game-frame identity. */
    uint32_t witness;
    uint32_t context_generation, drawable_generation, post_vi_origin, swap_count;
    /* SourceV2 only. Immutable renderer-owned default-FRONT admission state. */
    uintptr_t read_drawable;
    uint32_t drawable_width, drawable_height;
    uint32_t restore_read_framebuffer, restore_texture_2d, restore_read_buffer;
    uint32_t source_format; // rb_source_format; RGB8 raw bytes, alpha unspecified
} rb_surface;

enum rb_outcome { RB_OBSERVED = 1, RB_RETIRED, RB_NOT_OBSERVED, RB_SURFACE_FAILED };
/* rb_bind_source results; the numbers are stable. */
enum rb_install_result { RB_INSTALLED = 1, RB_ALREADY_INSTALLED = 2, RB_PIN_FAILED = 5,
    RB_BOUND_TO_OTHER = 7, RB_LIFECYCLE_REFUSED = 8 };

enum rb_image_ownership { RB_IMAGE_NONE, RB_IMAGE_SUBMITTED, RB_IMAGE_QUARANTINED };
typedef struct { uint64_t serial; uint32_t slot, epoch, ownership, status; } rb_image;
typedef struct {
    uint64_t occurrence;
    uint32_t epoch, slot, outcome;
    rb_stamp stamp;
    rb_surface surface;
    rb_image image; // retained even when the surrounding occurrence is cancelled
} rb_record;

typedef struct { uint64_t occurrence; uint32_t slot; } rb_ticket;
/* installed and observer_ready are equal: a bound source always observes. */
typedef struct { uint32_t offered, refused_full, refused_busy, observed, retired,
    unobserved, surface_failed, installed, observer_ready; } rb_stats;

typedef int (__cdecl *rb_capture_image)(const rb_record *, rb_image *);
typedef int (__cdecl *rb_image_completed)(const rb_image *);
/* Configure once before rb_bind_source/ROM startup; the callbacks stay resident.
 * Image callbacks may submit GPU work, never wait. Release guard runs only on
 * the single consumer and must report actual worker-use completion. */
RB_API int rb_configure_images(rb_capture_image, rb_image_completed);

/* Host serializes lifecycle with its own graphics API as LINK requires.
 * One background activation owner may race lifecycle/disarm; cancellation
 * invalidates delayed activation and renderer completions without a join. No
 * caller may use this as a replacement for LINK's original synchronization. */
/* Bind once pre-ROM; this module and the surface provider are pinned for the
 * process lifetime. The renderer's UpdateScreen command brackets its original
 * work with rb_source_begin/rb_source_end, and ordinary rendering is always
 * forwarded, including a refused/stale capture request. */
typedef int (__cdecl *rb_source_surface)(rb_surface *);
RB_API int rb_bind_source(rb_source_surface);
RB_API rb_ticket rb_source_begin(rb_ticket);
RB_API void rb_source_end(rb_ticket);
RB_API void rb_rom_open(void);
RB_API void rb_rom_closed(void);
RB_API void rb_close(void);
RB_API uint32_t rb_activate(void);
RB_API void rb_disarm(void);

/* Caller supplies the post-ProcessDList immutable stamp, then invokes original
 * UpdateScreen and calls finish. No allocation, waiting or GPU call here. */
RB_API rb_ticket rb_stage(const rb_stamp *stamp);
RB_API void rb_finish(rb_ticket ticket);
/* One consumer takes in admission order; an unfinished earlier occurrence
 * defers delivery. BORROWED records must keep that order through GPU/media work.
 * The consumer owns the returned record until release. Never recycle it merely
 * because delivery started: future GPU/encoder ownership must finish first. */
RB_API const rb_record *rb_take(void);
RB_API int rb_release(rb_ticket ticket);
RB_API rb_stats rb_get_stats(void);

#ifdef RB_TEST_HOST
/* Compiled out of deliverable builds: pause the claim, activation and take
 * paths inside their race windows. */
typedef void (*rb_test_probe)(void);
RB_API void rb_test_set_activation_probe(rb_test_probe probe);
RB_API void rb_test_set_take_probe(rb_test_probe probe);
RB_API void rb_test_set_claim_probe(rb_test_probe probe);
#endif
#ifdef __cplusplus
}
#endif

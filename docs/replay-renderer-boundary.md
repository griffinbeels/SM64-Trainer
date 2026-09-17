# Renderer boundary and bounded ownership

`plugin/gfxwrap/renderer_boundary.cpp` is compiled into the renderer overlay.
The [GPU runtime](replay-gpu-runtime.md) reaches it through the SourceV2 and
ContextV1 exports (`link_source_api.cpp`, `wrapper_runtime.cpp`), with bounded
occurrence and snapshot ownership. Accepted live on 2026-09-16.

## Binding and lifetime

The overlay binds its surface provider once, before ROM startup, with
`rb_bind_source`; a bind after RomOpen is refused, capture stays off, and every
frame still reaches the renderer. (An earlier adapter patched one original LINK
binary's command dispatch in place; the source build replaced it, and it was
deleted on 2026-09-16.)

The boundary module and its surface provider are pinned for process lifetime.
LINK's `CloseDLL` is empty, and its detached render thread may still be
returning after `RomClosed` signals completion, so releasing ordinary module
references does not establish safe unload. Shutdown disarms capture without
waiting; forwarding pointers and fixed data remain resident. Closing without
RomClosed, or repeating RomOpen, permanently refuses capture admission.
Replacing a pinned DLL requires restarting PJ64.

Original-source evidence: [command/lifecycle implementation](https://github.com/Luna-Project64/GLideN64/blob/d0d101054421dd74c6c398919112c7adfd8265e3/src/common/CommonAPIImpl_common.cpp),
[CloseDLL implementation](https://github.com/Luna-Project64/GLideN64/blob/d0d101054421dd74c6c398919112c7adfd8265e3/src/PluginAPI.h).

## Ownership and cancellation

One serialized emulation caller copies the post-ProcessDList stamp into one of
eight fixed slots before original UpdateScreen, then calls `rb_finish` after
forwarding. This retains the existing source-stamp convention; it does not
independently prove which pixels the original renderer produced.

The renderer claims that unique occurrence before original command execution,
then publishes its completion afterward. The occurrence participates in the
atomic slot-state comparison: a delayed callback or duplicate release cannot
claim or free a recycled slot. A consumer holds the completed record until
explicit release. No caller may access a record after release. The consumer
must order occurrences later; slot scanning is not chronological delivery.

Activation, ROM transitions and disable use cancellation generations. A delayed
activation cannot revive an old generation, and cancellation during either the
original call or observation retires its result. Generation exhaustion is sticky.
The serial uses a 32-bit atomic increment: MSVC's x86 64-bit `fetch_add` uses a
CAS retry loop, whereas the selected operation compiles to `lock xadd`. Ownership
uses lock-free 64-bit load/store and single strong comparisons, with occurrence
exhaustion refused before identity reuse.

Admission and consumption scan at most eight slots, independent of session
length. Metadata capacity is fixed (2048 stamp bytes plus fields per slot).
The renderer performs no explicit replay waits, allocation, logging, IPC,
readback or encoder work. Inactive calls reach no observer, timer or GL query.
This is a bounded code-path contract, not a guarantee about OS scheduling,
driver latency, cache contention or live gameplay cost.

A full pool immediately refuses admission. A missed boundary, cancelled
occurrence or observer failure is an explicit outcome. These are not complete
recording successes. Future media integration must expose missing/truncated
footage rather than inventing an input match or silently invoking synchronous
capture. GPU completion and encoder ownership must finish before slot release.

## Verification and remaining gates

Run `python tools/run_tests.py tests/test_renderer_boundary.py`. The real x86
C++ threaded host binds through `rb_bind_source` and verifies immutable stamps,
fixed-capacity refusal, stale release, recycled slot races, both cancellation
points, delayed activation across close, and a refused late bind. The lifetime
host pins a separate surface-provider DLL with a call held across CloseDLL-style
retirement and ordinary reference release. Deliberately broken ordering,
cancellation or occurrence checks must fail these tests. Live LINK pictures,
stamps and the whole chain were accepted on 2026-09-16; these hosts guard the
boundary's own contracts.

The [GPU snapshot](replay-gl-snapshot.md) is connected through optional
`rb_configure_images` callbacks, configured once before installation. A record
retains image ownership independently of its outcome: cancelled/retired records
still retain successful tickets. A null producer fence can leave queued GPU work
without a ticket, so `QUARANTINED` is distinct from `NONE` and cannot be released.
`rb_release` validates the borrowed occurrence before reading its attachment;
submitted images require proven worker return-fence completion. One consumer owns
record reads/releases; no access is allowed after release.

`tests/test_capture_chain.py` exercises both actual native components together
on hidden real WGL contexts. Independent GPU patterns prove immutable stamp/image
pairing despite repeated counters, source overwrite, delayed consumption, full
capacity, cancellation and GPU-slot reuse. A retained slot 3 can retire after a
new pool has only one slot. Dropped cancelled attachments, early completion and
checking old tickets against the new pool size must all fail. This is an FBO
fixture, not LINK/Usamune picture or production audio proof.

The next source integration is described in [LINK state observation](replay-link-state.md).

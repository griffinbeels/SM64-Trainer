# GPU snapshot ownership

`plugin/gfxwrap/gl_snapshot.cpp` is an owned-texture component, tested
with real x86 OpenGL contexts and connected to the [GPU runtime](replay-gpu-runtime.md).
The installation candidate still needs live gameplay verification.
The [renderer boundary](replay-renderer-boundary.md) can retain these GPU tickets
in the isolated combined capture host.

## Resource and thread contract

One serialized producer submits pictures; one worker prepares, polls, releases,
and retires their textures. The fixed CPU `Pool` must outlive every possible
producer and consumer invocation, including late calls and quarantined resources.
Contexts must share objects in the same process, with compatible implementation,
pixel format, and producer-supported GL capabilities. A newer worker context
alone cannot qualify an API for the producer. Function pointers are loaded once in that context family.

Preparation allocates at most eight RGBA8 textures, rejects requests exceeding
an explicit byte budget, and completes setup on the worker before publishing a
generation. The budget counts logical image payload, not measured driver VRAM.
Preparation refuses an existing unpack PBO. It reserves activation before driver
preflight; an any-thread stop defeats its final activation CAS. The control layer
must also reject stale preparation requests using its own demand generation.

When the caller qualifies the actual producer and worker as core OpenGL 4.5+,
the loader permits direct-state-access copying. First producer use still attaches
and restores each texture once to propagate worker-created shared state; later
producer writes use DSA without texture binding changes. Workers may read the
image but must not mutate its data or parameters. DSA is off without explicit
producer qualification, even on a newer worker. An explicitly tested older-GL path restores the supplied
texture binding; neither path reads state from the driver in the producer.

The producer performs one bounded slot scan, binds the supplied source, copies
the crop into an owned texture, restores bindings, inserts a fence, flushes its
own stream, and publishes an immutable occurrence/QPC/ticket. It performs no
allocation, readback, logging, IPC, encoding, GPU wait, or GL state query. It
refuses full capacity immediately. Neither pool size nor search cost grows with
session length. The GPU copy still costs O(width * height) bandwidth; it is not
free merely because no pixels cross the CPU.

`RestoreBindings` is **authoritative renderer-owned state**, not guessed defaults.
It includes the previous read FBO, current texture binding (first attachment and older-GL fallback), and the source FBO's
read-buffer selector. SourceV2 supplies that complete witness through the renderer
dispatch observer. The original pinned cache alone only
retains the last framebuffer bind call, not separate READ/DRAW bindings, and has
no read-buffer-selector cache. In particular, ReadScreen can leave the default
FBO set to FRONT while restoring COLOR_ATTACHMENT0 on another FBO. No-swap and
other renderer paths also prevent assuming a fixed post-UpdateScreen state.
The connected source tracks actual state mutation/context recreation inside
the renderer; upstream game textures omit
later composition/OSD and cannot silently replace the presented image.
The `SNAPSHOT_QUERY_BINDINGS` compile-time counterfactual restores the earlier
query-based prototype for measurement only; it must never select a product path.

The worker checks producer completion with zero timeout and rebinds the shared
texture before use. It keeps the slot BORROWED while reading, copying or encoding
from that texture. Release inserts a second fence **after worker GPU use** and
flushes the worker stream. Reaping requires that fence to complete before FREE.
The encoder bridge must not return this source just because a copy was queued.

Stop closes admission. Every successful submission ticket still needs draining,
even if its boundary record was cancelled; cancellation must not discard the only
ticket. Destroy refuses outstanding ownership. Missing fences or failed completion
queries quarantine the bounded allocation and disable capture; it cannot be reused
or re-prepared. Process/context retirement is the current quarantine endpoint.
A new generation does not reset ownership serials, so stale tickets stay invalid.

The worker records a per-slot completed-serial watermark only after its return
fence signals. `completed(ticket)` accepts genuine tickets from this same
process-lifetime pool and can retire old metadata even after that GPU slot is
reused or the pool is recreated with fewer slots. It is not a validator for
arbitrary/forged IDs. Watermarks survive destroy/reprepare; fixed slot bounds,
not the new active count, determine which old tickets can be checked.

## Pixel and synchronization limits

A signaled fence proves command completion, **not that a void GL copy succeeded**.
The caller must qualify actual source dimensions, format, framebuffer/sample
rules, and device-loss/error handling. The snapshot component does not consume
LINK's GL error flags; the connected SourceV2 BeginCapture/EndCapture gateways
own that policy and preserve consumed diagnostics for original renderer reads.
Submitted alone is not a verified/exact-picture outcome. The standalone fixture
does not establish the source gateway or real game's pixel/state association.

The fixture uses a linear RGBA8 FBO. Hidden default front buffers are subject to
window-system pixel ownership; this does not establish LINK's post-swap GL_FRONT,
OSD/composite output, crop under arbitrary resizing, or Usamune picture/stamp
association. Those require the existing independent live pixel oracle. It also
does not establish actual LINK pacing, other GPU vendors, encoder import,
compressed PTS, audio synchronization, or instant replay readiness.

Producer glFlush is necessary for the final picture when no later render occurs.
It is not a completion wait, but driver APIs provide no bounded latency guarantee.
No explicit added wait is a code property; unchanged gameplay feel still requires
measurement of the original renderer before/after, including following calls.

## Verification

Run `python tools/run_tests.py tests/test_gl_snapshot.py` from the worktree. The
host creates two hidden WGL windows and separate producer/worker threads; it
never opens PJ64, the server, or a visible window. Worker readback is solely an
independent test oracle, not a fallback capture implementation.

Tests check 17 asymmetric rendered patterns through crop (1,2), source overwrite,
out-of-order consumption, exact metadata, texture/read-FBO/read-buffer/active-unit
restoration, fixed budget, held borrowed slots, pending return fences, stale
tickets, final picture without another producer GL call, preparation cancellation,
unpack-PBO refusal, and three injected completion failures. Intentionally omitting
the copy, changing its crop, or freeing a pending return must fail.

On one RTX 5090 fixture run, query-based submission averaged 649.565 us with an
8096.5 us maximum; supplied binding state averaged 7.271 us with a 13.6 us maximum.
These are small 8x6 fixture CPU submission timings, **not an end-to-end speedup**.
Post-submit test queries may absorb deferred work. A larger uninterrupted renderer
cohort, source preparation costs, actual GPU memory, and GPU contention remain
unmeasured. First-use samples remain included; no pacing claim excludes them.

Primary contracts: [ARB_sync](https://registry.khronos.org/OpenGL/extensions/ARB/ARB_sync.txt),
[shared-object visibility](https://registry.khronos.org/OpenGL/specs/gl/glspec46.core.pdf),
[WGL sharing](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-wglsharelists),
[copy semantics](https://raw.githubusercontent.com/KhronosGroup/OpenGL-Refpages/main/gl4/glCopyTexSubImage2D.xml).

## Source error admission

Producer submission now checks the source error boundary after claiming a free
slot, before any capture GL mutation, and again after copy/restoration/fence/flush
but before QUEUED publication. A preflight refusal returns unqualified without
GPU custody and stops admission. A postflight failure quarantines the allocation;
a successful fence alone cannot publish stale pixels. Full and passive calls do
not run these checks. Gl::load uses a raw error check for standalone hosts; real
source integration must replace both callbacks with its diagnostic-preserving,
state-invalidating gateway. These checks add no explicit wait, but GL calls can
incur driver latency and still require live measurement.

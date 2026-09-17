# LINK renderer state observation

The SourceV2/ContextV1 overlay supplies the renderer boundary and state witness
used by the [GPU runtime](replay-gpu-runtime.md). The shipped renderer and
wrapper were accepted live on 2026-09-16 (picture, smoothness, audio and the
input timeline).

## Source and build

Pinned source: [Luna-Project64/GLideN64 d0d1010](https://github.com/Luna-Project64/GLideN64/tree/d0d101054421dd74c6c398919112c7adfd8265e3).
`tools/stage_link_witness.py --source <extracted archive> --out <new .iteration directory>`
verifies the whole archive tree and patch targets, refuses linked entries, and
stages a separate copy. It never edits the baseline, builds or installs a DLL.
`tools/build_renderer.py` stages the overlay (including context lifetime) and
always defines `SM64_REPLAY_GL_WITNESS`, `SM64_REPLAY_SOURCE` and
`SM64_REPLAY_CONTEXT_LIFETIME`; the wrapper build does not include it. The
toolchain, the prebuilt static Qt libraries and why the build is a dependency
adaptation rather than a byte reproduction of LINK's stock DLL are recorded once
in [the renderer build](../renderer/README.md).

## Explicit source command API

`--boundary` also stages a versioned `SM64ReplaySourceV2` export. Registration
is immutable before ROM startup; callbacks have explicit x86 calling conventions
and their modules remain pinned across detached-renderer completion. A malformed
capture request still forwards exactly one normal update. The immutable ticket
travels by value in LINK's existing command; observation follows its ordinary
`VI_UpdateScreen` and precedes the original acknowledgment. No second update,
new wait or queue is added. Retain the old post-ProcessDList stamp bytes and
post-forward VI-origin acceptance rule in the connected wrapper.

`--label` changes only the test build's selector label. `pluginName` and settings
identity remain unchanged. Source manifests distinguish that label edit from
rendering changes. The original archive stays immutable.

The public API and command plumbing remain separate from verified pixels.
SourceV2 retains the complete read drawable and restoration state; the source
capture gateways below qualify submission separately. Null-callback registration
consumes the one-shot setup, so the wrapper preregisters dormant real callbacks
to permit hot activation.

`tests/test_renderer_source.py` compiles complete modified source units and
executes the actual modified command body with an independent original-call
witness. `tests/test_renderer_source_abi.py` loads the real API implementation
as a DLL, validates its query/registration/lifecycle and pinned callback modules
through a consumer compiled with a different default calling convention. The
latter uses a fixture platform surface; neither test proves LINK's actual pixels.

## Observations and qualification

`renderer_gl_state.h` retains fixed READ/DRAW FBO, default read-buffer selector,
active texture unit, and 32 per-unit 2D bindings. Deletion updates implicit
unbinds; deletion bookkeeping is bounded. Unknown state refuses a witness.
Identity includes context/read drawable, their generations, and owning thread.

`link_dispatch.cpp` observes actual driver dispatch, including the framebuffer
function pointer captured by LINK's cache. It replaces three extension slots
only after upstream resolves them; three Windows core aliases cover direct
ReadBuffer/BindTexture/DeleteTextures calls. Cache behavior is unchanged. The
pinned Windows source has no relevant named-selector, multibind or alternate
proc-loader bypass; adding such a route requires extending this coverage.

Context creation uses actual current handles and pixel-format double buffering,
only for a freshly created context. Hot activation cannot manufacture defaults.
Dispatch installation belongs to that context generation. A resize-induced
context recreation without a function-table reload stays unavailable. A resize
of the same context advances drawable generation while preserving bindings.
Generation overflow invalidates observation, without wraparound reuse.

Observers add no driver queries, waits, allocations, I/O, events or capture work
per mutation. GL capability/current-handle queries occur during preparation.
This is a source-code property, not measured zero gameplay overhead.

`CandidateBindings` is deliberately unqualified. Void GL calls do not report
success. Known-illegal selector/framebuffer combinations invalidate observation
without draining the renderer's error flag; arbitrary allocation/driver failures
still need a separate source/error/device-loss policy. A signaled copy fence
cannot prove successful pixels. Never feed this candidate directly into a
verified-picture path until those gates and source composition are established.
Raw capture operations must bypass cache/observer calls and restore exact known
state; failed/incomplete restoration must invalidate source admission.

## Verification

Set `SM64_LINK_WITNESS_SOURCE` to the staged directory and run
`python tools/run_tests.py tests/test_link_source_witness.py` in the worktree.
Without staged third-party source those tests explicitly skip; that is not proof.
The positive host compiles LINK's actual loader, caches and GL parameter definitions,
then exercises real hidden WGL state. It checks cache no-ops/reset, split FBO
bindings, texture units/deletion, default selector, illegal screenshot selector,
resize identity, context recreation and recursive-install refusal. Host-only GL
queries independently inspect state, including a temporary raw default-FBO bind
that is fully restored. Removing either captured-pointer or core-selector
observation must fail. A separate compile check builds the modified Windows
lifecycle translation unit; no live PJ64, server or installed DLL is touched.

The combined [capture-chain tests](replay-renderer-boundary.md) separately prove
GPU ticket/metadata custody. The whole LINK-to-encoder-to-input-timeline chain
was accepted live on 2026-09-16; these witnesses guard its parts.

## SourceV2 capture connection

The versioned surface retains the read drawable, committed client extent and
three exact restore bindings in each immutable record. V1 queries are refused;
the x86 surface/record/API layouts are asserted in the public header. Resize
observes the original SetWindowPos result and then GetClientRect, never guesses
that crop dimensions equal the whole drawable.

Source-owned BeginCapture/EndCapture are called inside the snapshot slot's
exclusive submission interval. They perform one raw error query each; a saved
error remains visible to the next original renderer diagnostic read. Original
error readers (including checked templates) invalidate nominal state too. This
preserves the consumed diagnostic, not identical driver error-flag behavior.
The gateway is serialized source-renderer-thread code; delivery-worker GL uses
raw dispatch. Context loss discards that context's saved diagnostic.

source_snapshot.cpp binds these gateways during worker preparation and connects
immutable records to a fixed default-FRONT pool. It refuses changed contexts,
drawables, generations and dimensions before copy. It preserves the crop and
occurrence/QPC, and returns image custody only after actual worker completion.
A real hidden FRONT-buffer witness preserves pixels across source overwrite and
captures a retained image without another swap. Its platform descriptor is a
fixture; live integration evidence is the 2026-09-16 acceptance.

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

Practice-ROM window creation prefers an actual RGB8 Windows pixel format when
the driver's closest choice uses different RGB sizes. The selector preserves an
already-supported choice, existing window storage, depth/stencil requirements,
requested alpha and baseline cartridges. Windows permits one format per window;
an existing non-RGB8 window keeps its storage until a fresh window is created.
The later GL component-type/color-encoding checks still qualify capture; a
Windows descriptor never establishes those GL facts. The renderer's ghost scopes
and fixed-RGBA8 presentation rollback retain their existing admission rules.
See [Windows format matching](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-choosepixelformat).
Real-driver ghost tests exercise an actual non-RGB8 candidate, the corrected
selection, preserved pixels/state, and a deliberately restored bad selection.

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
function pointer captured by LINK's cache. It replaces extension slots only
after upstream resolves them (framebuffer bind/delete, active texture, depth
range, program, vertex array bind/delete, indexed enable/disable); Windows core
aliases cover direct ReadBuffer/BindTexture/DeleteTextures, Viewport, Scissor,
DepthFunc, DepthMask, BlendFunc and Enable/Disable calls. Cache behavior is
unchanged. The pinned Windows source has no relevant named-selector, multibind,
separate-blend, program-pipeline or alternate proc-loader bypass; adding such a
route requires extending this coverage.

What the ghost's frames read without asking the driver: `Raster` (draw
framebuffer, viewport, depth range), `Render` (program, vertex array, depth
function and mask, blend factors, scissor box), `Enabled` (the capabilities
the ghost restores) and `Texture2D`/`ActiveUnit` (per-unit 2D bindings). Each
starts from a fresh context's defaults, or is unknown until the renderer's
first call where no default is certain (viewport, scissor box); any GL error
the gateway sees invalidates them with the rest of the observation, until the
[witness rebuild](#witness-rebuild) asks the driver for all of them again.

Context creation uses actual current handles and pixel-format double buffering,
only for a freshly created context. Hot activation cannot manufacture defaults.
Dispatch installation belongs to that context generation. A resize-induced
context recreation without a function-table reload stays unavailable. A resize
of the same context advances drawable generation while preserving bindings,
including while the observation is invalid, so a rebuilt witness names the
drawable it is in. Generation overflow invalidates observation, without
wraparound reuse.

Observers add no driver queries, waits, allocations, I/O, events or capture work
per mutation. GL capability/current-handle queries occur during preparation
and in the witness rebuild, which runs only on a refusal, at most once a second.
This is a source-code property, not measured zero gameplay overhead.

`CandidateBindings` is deliberately unqualified. Void GL calls do not report
success. Known-illegal selector/framebuffer combinations invalidate observation
without draining the renderer's error flag; arbitrary allocation/driver failures
still need a separate source/error/device-loss policy. A signaled copy fence
cannot prove successful pixels. Never feed this candidate directly into a
verified-picture path until those gates and source composition are established.
Raw capture operations must bypass cache/observer calls and restore exact known
state; failed/incomplete restoration must invalidate source admission.

## Bindings cause

Every route to a false `CandidateBindings` reports the same `0x53520001`
refusal, so the overlay also records why (`source_refusal.h`). Each of the
twenty sites that leave the observation unarmed records itself; the first one
since `ContextCreated` stands, with the GL error that site read, a count, and
the milliseconds since creation. Flags say which arming steps happened:
created, practice ROM, armed, dispatch installed for this context, nominal,
owner thread, context lost, and which state-witness parts are known. A BINDINGS
refusal copies the record into the refused surface's unused bytes 52..75;
identity, origin, swaps and source format stay zero. These are writes of
values each site already holds: no query, wait or allocation. After a
successful rebuild the next invalidation is recorded as a new first site;
the `structural` flag says this context can no longer be rebuilt.

`ContextCreated` also reads `GL_VENDOR`, `GL_RENDERER` and `GL_VERSION` once,
for a practice ROM with its handles current. A context that call never saw is
read once, at its first bindings refusal. The wrapper copies them through the
`SM64ReplayContextEvidenceV1` export without GL; an older renderer lacks it,
which reads as unknown. Strings are printable ASCII, bounded, NUL-padded.
The same export counts this context's rebuild attempts and successes and
names what the latest success repaired.

## Witness rebuild

Until 2026-10-24 any invalidation was one-way until the next context, so one
GL error left pending at the first capture gateway (for example by an init
step GLideN64's Release build never checks) cost GPU recording for the whole
session. His ruling that day: *"Why can't we fix the GPU stuff as best we
can? Try to do it as best we can without needing to run on my GPU."* So
`replay_gl::Reconstruct` now rebuilds the witness from the driver's answers.

- **When.** Only when `surface()` is about to refuse with BINDINGS, at most once
  a second per context, then `CandidateBindings` is asked again. The context
  must be the armed practice-ROM context, current on its owner thread with the
  dispatch installed for its generation, GL 3.3 or newer, not lost.
- **Recoverable sites only** (`rs_bindings_site_recoverable`): 1-4, 11-15
  and 17, an error flag or one call capture could not follow while every hook
  stayed installed. Every other site is structural (hooks missing, another
  thread, drawable or context), and any structural site since creation blocks
  rebuilding for the context: later mutations could go unobserved.
- **How.** Drain the driver's error flags (at most eight). The first stays the
  renderer's pending answer; out-of-memory or a lost context ends rebuilding
  for the context. Then ask the read and draw framebuffer bindings, the active
  unit and its 2D texture (other units are learned again as the renderer binds
  them), the default framebuffer's read buffer only while it is the one bound
  for reading (GLideN64 binds it before every swap), viewport, depth range, and
  with every render-state hook installed the program, vertex array, depth
  function and mask, blend factors, scissor box and the eleven capabilities.
  A query error is ours: drained, never handed to the renderer, and the
  witness stays invalid until the next second.
- **The renderer's answer.** A saved error is returned exactly once by the
  renderer's next `glGetError` and never invalidates again. It no longer
  refuses the gateway: the Release build never asks for it.
- **The ghost steps aside.** A ghost-only scope that closes dirty (the draw
  glue's display scope or setup, the presentation's clean save, ghost VI draw,
  OSD mirror or display copy) stops the ghost for that context, so an error
  repeated every list costs the source one rebuild, not one per list. A
  drained layer error still drops only that list's layer.
- **Evidence.** Each success writes one `bindings_reconstructed` line (at most
  sixteen an epoch); a failure line carries `reconstruct_attempts` and
  `reconstruct_successes`.

What it cannot do: an error the renderer raises every frame still refuses
every picture between rebuilds, and while recording is active one refused
picture still ends that recording (`GD_SOURCE_IMAGE_MISSING`) until the
retry gate starts a new one. A structural site, a fatal driver error, or a
presentation whose own setup fails deterministically stays unavailable for
the context. Only a recording request rebuilds: with recording off, a ghost
waits for the next context. `tests/test_link_dispatch_cpu.py` runs the real
overlay over a fake driver; the `heal` mode of
`tests/test_link_source_witness.py` checks it against a real one.

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
error remains visible to the next original renderer diagnostic read, once, and
a later consumed error never replaces it. Original error readers (including
checked templates) invalidate nominal state on a fresh driver error. This
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

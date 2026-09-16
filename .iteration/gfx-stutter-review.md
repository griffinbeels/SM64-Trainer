# Native wrapper stutter review

Checkout: `.codex/gfx-stutter-fix`, branch `codex/gfx-stutter-fix`, base
`2d5c5fb607f18a16f2370597a00d717eacc64122`.

## Premise and scope

The parent supplied the user's controlled A/B: he changes the Project64
graphics plugin himself and restarts Project64. LINK's GLideN64 v4.2 is smooth
over extended play; LINK's GLideN64 v4.2 +SM64 Trainer stutters several times
per second even with the trainer closed. This is accepted evidence. Earlier
registry-only selection did not change the UI's selected plugin and cannot
exonerate the wrapper.

Preserve picture, stamp, input and audio semantics. No application changes,
registry writes, installs, live capture, server restarts or main merge. Parent
owns integrated build identity, installation guidance, Python diagnostics and
final integration. This reviewer owns native source and its focused tests.

## Plan before edits

1. Trace all exports, loading/unloading, shared mappings, lease, profiling,
   GL readback and logging; separate concrete defects from possible causes of
   the user's stutter.
2. Remove capture and GL probing work from inactive callbacks. Fix concrete
   lifecycle/readback faults without changing the frame association contract.
3. Add bounded native diagnostics with process/build/path identity, lifecycle,
   failure and callback stall evidence. Never write files on a frame callback
   or wait on a logger from DllMain.
4. Exercise production C through controlled CPU mocks and the existing native
   host, with injected failures/delays and sensitivity checks. Preserve the
   shipped binary until the parent builds the integrated candidate.
5. Review the diff, commit owned paths, and hand off findings and honest limits.
   Synthetic tests cannot prove the user's real renderer is now smooth.

## Findings and candidate

The user's stutter is **not yet proved fixed by live play**. This review found
and fixed concrete defects; it did not reproduce the real renderer's stutter
under the native fake. The strongest remaining causal candidate addressed here
is the wrapper's premature OpenGL initialization/probing, including its first
`wglGetCurrentContext` call before loading/initializing GLideN64. The effect on
that driver's background work is a hypothesis, not a measured explanation.

| Area | Finding and change | Evidence/limit |
| --- | --- | --- |
| Inactive callbacks | Removed all GL context probes before/after forwarded calls and before renderer initialization. `ProcessDList` copies RAM only under a live capture lease. Header liveness/list counts still advance. | Production C runs under observable CPU substitutes. Inactive and expired-lease paths make zero context, RAM-copy, VirtualQuery or ReadScreen calls. Mutations restoring GL calls or unconditional stamping both fail. |
| Diagnostic file I/O | Old context-change logging opened, wrote and closed the log on the emulator callback thread. Replaced with bounded nonblocking queue and asynchronous writer; demand/context events rate limited too. | Existing live log was only 12,296 bytes and recent callbacks had stable null contexts: no observed log flood in this reproduction. This is a concrete stall hazard fixed, not its established cause. |
| Static CRT | Removed `DisableThreadLibraryCalls` from a `/MT` DLL. | Microsoft explicitly prohibits that combination because static CRT needs thread notifications. This is an API contract defect; its contribution to visible stutter remains unknown. |
| Exports | Added the missing `FBWList` extension with unchanged opaque pointer/count forwarding. A wrapper no longer hides missing core required exports in the wrapped DLL. Other Zilmar calls preserve arguments/order and remain direct forwarding. | Installed LINK DLL's PE exports include FBWList. Official GLideN64 source confirms its signature. CPU test checks exact forwarded pointer and count. Exact Wermi v7 host usage of this optional extension was not established. Private GLideN64 config/UI/osal exports are not host graphics ABI and are not reexported. |
| Loading/dialogs | Temporary DllAbout/DllConfig/DllTest loads release their wrapped module outside loader lock. Relative subpaths resolve beside the wrapper. A named marker rejects renamed wrapper copies as well as the same HMODULE. | Renamed-copy recursion has a native host regression. GetDllInfo enumeration still reads only INI and starts no worker or wrapped load. |
| Lifecycle | `CloseDLL` now closes frame/profile mapping handles and clears active flags. Reinit recreates profile producer and lease state. Failed wrapped init never advertises initiated. | Repeated-session native test checks fresh profile counts. Failed-init native test checks inactive header. Host now actually calls FreeLibrary after CloseDLL. Old open_stream reused g_hdr, so it did **not** allocate another large mapping on every InitiateGFX; the defect was retained resources/state. |
| Shared memory/RAM | Requested mapping view size is explicit, refusing an undersized preexisting mapping. RAM table claims clamp to hardware maximum; overflow-safe span check. | Existing 4MB and late-commit expansion tests remain green. Picture-stream/profile ABI unchanged. |
| Capture lease/stamps | Demand checked at both list and VI. Losing/resuming demand clears pending stamp; a picture arriving before the next captured display list stays explicitly unstamped. | CPU test covers abandoned reader, renewal between list and VI, and exact next active picture. No old stamp is attached to resumed pixels. Steady-state capture timing/association is unchanged. |
| GL state | Entry points follow the actual capture context and invalidate at RomOpen/ChangeWindow; invalid procedure sentinels are rejected. Removed unbounded error draining which consumed renderer-owned errors. | Existing FBO/PBO/pack-state host and CPU contracts pass. A mutation restoring error draining fails the error-query observer. Actual supported renderer configurations remain a live verification boundary. |
| ReadScreen | Preserved synchronous ordered screenshot acquisition, packed BGR copy, process-heap validation and freeing. Added sparse empty/invalid-size/free-failure evidence; free failure retires that route. | No attempt to bypass renderer ordering, move readback across frames, or change pixel ownership. Both fake capture paths retain identical colors/stamps. Unknown heap blocks still retire after one retained buffer. |
| Profiling | Existing opt-in profile ABI, heartbeat expiry and five-minute bound unchanged; reset on each new producer. | Native profile lease/duration test and Python reader tests pass; injected 25ms ReadScreen delay remains attributed to that stage. |

## Diagnostics contract

- Existing `sm64_trainer_gfx.log` beside wrapper, rotated to `.log.1` at 1MiB.
- UTC line prefix `YYYY-MM-DDTHH:MM:SS.mmmZ pid=... tid=... build=... event=...`.
- `init_begin`, `wrapper_loaded path="..."`, `wrapped_loaded path="..."`,
  `init_result`, wrapped name/version, stream availability, ROM/configuration
  lifecycle, capture demand/path, failures and shutdown.
- `callback_stall` for callbacks at least 20ms or call gaps at least 50ms,
  at most once per five seconds per callback; quiet summary once per minute.
  Includes total/wrapped/extra/gap durations, cumulative maxima, calls and
  slow-call count. Gaps also include legitimate pauses, and these are CPU wall
  times, not GPU render-thread timings.
- A 64-record fixed queue per logger session; enqueue uses only try-locks.
  Full/busy queues count dropped diagnostics. File writing and rotation happen
  on its worker; no per-frame file logger, periodic worker polling or GL probes.
- `CloseDLL` detaches the logger, signals it and waits at most 200ms outside
  DllMain. The writer owns a module reference until FreeLibraryAndExitThread.
  A new session gets independent queue/lifetime even while old storage is slow.
- Test blocked an actual writer, overfilled the queue, observed diagnostic
  drops, and started a new logger before releasing the prior blocked writer.
- Parent build tooling supplies reproducible `GFXWRAP_BUILD_ID`; standalone
  source has explicit `development-unidentified` fallback. A startup line
  indicates actual InitiateGFX, not DLL enumeration.

## Verification

Final focused command, from this checkout, using parent's existing Python:

```
C:/Users/griff/Desktop/code/sm64_tracker/.codex/replay-review/.venv/Scripts/python.exe tools/run_tests.py tests/test_gfxwrap_cpu.py tests/test_gfxwrap_host.py tests/test_graphicsprofile.py -q
```

Result: **31 passed**, exit 0, no selected-test skips. Eight CPU native cases,
17 native host cases and six Python profile cases. Includes deliberate negative
controls for inactive GL/stamping, FBO selector restoration and error draining.
The runner warned that uilab was unavailable; these native/profile targets have
no rendered-UI gate. `git diff --check` passed. Automatic quick verification in
this isolated tree was unavailable because pinned Ruff identity/local ESLint
dependencies were missing; no quick pass is claimed. Parent owns the integrated
quick/full and built-DLL consumer gates.

Shipped and installed DLLs were untouched. The existing shipped-DLL host case
still passes its older active-pixel contract; it does not establish that the
new inactive/lifecycle fixes are installed. Parent must build and validate the
candidate's forced build ID before preparing installation.

## Remaining limits

- The six maximum-size slots still reserve/map **149,327,872 bytes** (~142.4MiB,
  149.3MB) in Project64 even without capture demand. Inactive callbacks touch
  the header only; virtual-address/commit pressure is still a possible cost.
- Premature OpenGL initialization, missing FBWList and static-CRT misuse are
  addressed; none is independently established as the cause of this real
  several-times-per-second stutter. Do not present the passing fake as proof.
- No emulator/server restart, registry write, live capture, driver change or
  installed DLL replacement occurred. No input/audio behavior was changed.
- GPU work queued by the wrapped renderer is not timed directly by native
  callback timing. Fast callbacks with continued visible stutter leave that
  asynchronous work unresolved; parent diagnostics/traces can retain evidence.
- A host that violates CloseDLL before unloading after successful init can
  leave the logger's module reference alive until process exit. A stalled
  storage operation can retain its bounded logger session until I/O returns.
- Abrupt process death can lose queued log records. Lack of a log record is
  not evidence that a callback was fast; dropped-record counters are reported.

## Primary references opened

- [Microsoft: DisableThreadLibraryCalls](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-disablethreadlibrarycalls)
- [Microsoft: DLL lifecycle and loader-lock guidance](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-best-practices)
- [Microsoft: context-specific wglGetProcAddress](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-wglgetprocaddress)
- [GLideN64: common API forwarding, including FBWList](https://raw.githubusercontent.com/Luna-Project64/GLideN64/4.0-angle/src/CommonPluginAPI.cpp)
- [GLideN64: framebuffer extension contract](https://raw.githubusercontent.com/gonetz/GLideN64/master/src/FrameBufferInfoAPI.h)

Learning harvest: durable native contracts are pinned in executable tests and
comments at the affected code; no new global process rules were added. Parent
owns incident/history consolidation and public consumer documentation.

## Reopened after the installed candidate failed, 2026-09-10

**The candidate did not solve the user's problem.** The parent verified actual
InitiateGFX in PID 48688 with build
`ecb40287375a7859293cba77e565b535414b0b87ed5ae2cd39aef1e45f4c067b`, installed
SHA-256 `8ea664466053a8def86935e27ec26bd9109a735fc6d3b8159d26b2f4a4cc44fa`.
The user reports severe lag, worsening over time, with seconds of crackling
and pitch distortion in Project64 audio. The fixes and synthetic tests above
remain useful contract repairs; the earlier premature-GL-probe causal proposal
is insufficient and must not be reported as the explanation.

This follow-up inspected installed PE bytes, source and saved measurements
read-only. Only this artifact changed. No DLL, source implementation, emulator,
server, process settings, registry or capture configuration changed here.

### Retained live evidence and its limits

Parent evidence is under `.codex/replay-review/.iteration/replay-review/round-16/`:
`live-logs-01`, `audio-stall-02`, `replay-after.json`, `thread-affinity.json`.
The first log set was read here; later profile/affinity figures below were
reported by the parent from its own collection.

- At 02:04:08.923 UTC, before capture demand first activated at 02:05:08.811,
  UpdateScreen took 108.664ms: 108.663ms wrapped, 0.001ms wrapper extra.
  This establishes an inactive-period wait inside the forwarded call. It does
  not establish why the wait occurred or equate a startup event to the ongoing
  several-times-per-second symptom.
- At 02:10:31.369, UpdateScreen took 27.500ms: 0.669ms wrapped and 26.831ms
  extra. Later parent logs show maximum extra reaching 77.616ms. Stage profiling
  was not active at those worst events, so these cannot yet be attributed to
  ReadScreen, copy, heap cleanup or scheduling individually.
- A later 20-second profile contains 579 ReadScreen calls, mean 2.225ms,
  maximum 19.1227ms. Combined copy/free mean 0.502ms, maximum 1.1178ms;
  UpdateScreen maximum 20.089ms. This locates most capture cost in the
  renderer-call boundary during that window. It does not characterize the
  earlier audio-corruption spike.
- Current dimensions are 1600x1200, packed BGR row 4800 bytes. Row alignment
  is valid in this run. General odd-width screenshot allocation remains a
  renderer-contract concern, not this reproduction's explanation.
- All 50 observed Project64 threads have affinity mask 0xffffffff; the
  emulation thread 32296 and dominant threads 53796/41272 have priority 0,
  process normal priority. There is no current pinning/priority evidence.
- The process loads NVIDIA OpenGL/overlay and DiscordHook modules. Presence
  is not fault attribution. No GPU queue, DPC, presentation, audio-thread or
  driver-stack evidence was collected by this reviewer.

### Exact renderer boundary: source corroborated by installed machine code

The `4.0` branch at commit
`d0d101054421dd74c6c398919112c7adfd8265e3` explicitly reports LINK's GLideN64
v4.2. Its RSPTHREAD implementation matches the installed public command
dispatch disassembly. This establishes the relevant implementation structure;
it is not a reproducible-build proof that every byte came from that commit.
Newer `4.0-angle` source is structurally different and must not substitute for
this evidence.

| Installed LINK RVA | Verified behavior |
| --- | --- |
| 0x8590 | API singleton constructor initializes mutexes/condition variables, null command and null thread state. It starts no render thread. |
| 0x6d7f0 | GetDllInfo calls singleton getter, fills version 0x103, type 2, name, NormalMemory 0 and MemoryBswaped 1. |
| 0x86a0 | InitiateGFX copies the expected GFX_INFO fields, enumerates child windows, returns 1 using cdecl. No discovered stack/layout mismatch. |
| 0x6d660 -> 0x9690 | RomOpen starts the renderer thread; thread creation call is at RVA 0x9784. Wrapper forwards this call on the host's same caller thread. |
| 0x87f0 / 0x88e0 / 0x6d830 | ProcessDList / UpdateScreen / ReadScreen construct command objects on the caller stack and dispatch through RVA 0x9230. |
| 0x9230 | Single-command handoff, notify renderer, wait for completion, then clear command pointer. This is synchronous, not an unbounded asynchronous queue. |
| 0x9540 | ReadScreen command invokes DisplayWindow's read-screen virtual operation with the original destination/width/height pointers. |

Matching source:

- [Singleton, RSP thread, synchronous command handoff, RomOpen and ReadScreen](https://github.com/Luna-Project64/GLideN64/blob/d0d101054421dd74c6c398919112c7adfd8265e3/src/common/CommonAPIImpl_common.cpp): lines 22-64, 193-223, 295-302.
- [Constructor and Windows versus Mupen API declarations](https://github.com/Luna-Project64/GLideN64/blob/d0d101054421dd74c6c398919112c7adfd8265e3/src/PluginAPI.h): lines 53-82, 92-113.
- [Windows identity explicitly naming LINK v4.2](https://github.com/Luna-Project64/GLideN64/blob/d0d101054421dd74c6c398919112c7adfd8265e3/src/windows/ZilmarAPIImpl_windows.cpp): lines 27-34.
- [Windows swap and screenshot readback](https://github.com/Luna-Project64/GLideN64/blob/d0d101054421dd74c6c398919112c7adfd8265e3/src/Graphics/OpenGLContext/windows/windows_DisplayWindow.cpp): lines 154-159, 296-320.
- [OSD/swap ordering and readScreen dispatch](https://github.com/Luna-Project64/GLideN64/blob/d0d101054421dd74c6c398919112c7adfd8265e3/src/DisplayWindow.cpp): lines 34-45, 179-186.

ReadScreen allocates width*height*3, binds the default read framebuffer, reads
GL_FRONT into client BGR memory, and restores read selection/framebuffer using
the renderer's state. The wrapper therefore introduces a second synchronous
render-thread round trip and completed GPU readback for each captured picture.
That mechanism can delay the emulator and its audio production when the GPU
or renderer worker stalls. It is a concrete capture cost and plausible
amplifier, **not proof of the complete reported cause**, especially the original
trainer-closed A/B. An unsignaled readback cannot explain a callback that never
requests it.

### Import graph and rejected hypotheses

Installed PE import order:

- Wrapper: OPENGL32, USER32, KERNEL32.
- LINK: WS2_32, NETAPI32, USERENV, VERSION, dwmapi, WTSAPI32, IMM32,
  OPENGL32, WINMM, KERNEL32, USER32, GDI32, ADVAPI32, SHELL32, ole32, OLEAUT32.

Both statically import OPENGL32 and neither has a delay-import table. Wrapper
GL imports are wglGetProcAddress, glGetIntegerv, glPixelStorei, glReadBuffer,
glReadPixels and wglGetCurrentContext. LINK imports all of those except
wglGetCurrentContext, plus its renderer GL entrypoints. Wrapper loading can
therefore load OPENGL32 earlier, including wrapper-only enumeration before
the original renderer is loaded. Removing explicit probes did not remove
that loader boundary. No specific harmful initialization or hook action was
proved. Merely finding OPENGL32 in the wrapper is not causal evidence because
the smooth direct renderer also requires it.

Both DLLs advertise preferred base 0x10000000 and ASLR. Image sizes are
0x26000 wrapper and 0xd8d000 LINK. Preferred-base collision does not establish
a relocation defect. The remaining 149MB frame mapping changes address-space
and commit pressure even while idle; its contribution is unmeasured.

Rejected or unsupported explanations:

- Wrong installed candidate: contradicted by matching startup identity.
- GetDllInfo versus InitiateGFX singleton construction creates a renderer on
  a different thread: contradicted by exact source and binary. RomOpen creates
  that thread in both cases; constructor work contains no thread creation.
- Original callback ABI is obviously corrupt: export signatures, cdecl stack
  handling, PLUGIN_INFO values and GFX_INFO field positions checked here do
  not show that defect. This does not certify undocumented Wermi host behavior.
- Logging flood: original retained logs do not show it; new logging is sparse.
- Heap validation dominates current capture: contradicted in the profiled
  window, but the worst spike was outside it. Do not remove allocator safety
  based on an unmeasured hypothesis.
- Asynchronous command backlog inside this LINK version: its command handoff
  is synchronous with one stack command, so newer upstream queue behavior does
  not describe this DLL.

### Minimal always-on evidence for the next actual stall

Proposed small instrumentation change only; not implemented in this follow-up:

1. Retain a fixed current-UpdateScreen detail record: route, capture wanted,
   origin, picture sequence, dimensions, pending list identity, ReadScreen
   ticks, pixel-copy ticks, heap-validate/free ticks and publish ticks. Measure
   only stages already executing; no extra GL inquiry, allocation or I/O.
   QPC at stage boundaries replaces dependence on the opt-in profile window.
2. On each completed callback, retain the entire detail record for the worst
   extra-time event in the reporting interval, alongside its wrapped/total
   duration and occurrence QPC. Keep lifetime maxima with their stage snapshots
   too. A throttled later line must name the original event time/sequence,
   rather than pairing an old maximum with a recent fast callback's stages.
3. Emit the compact snapshot through the existing bounded asynchronous queue
   on the existing stall/summary schedule. Include diagnostic drop counts and
   explicit stage presence flags. Separate copy from validation/free so the
   allocator hypothesis is actually testable.
4. Sensitivity tests inject a delay independently in ReadScreen, copy and
   cleanup, including a worse event suppressed by the five-second throttle.
   The next emitted record must retain the correct worst event and stage.
   Inactive-path tests must still observe zero capture/GL work. These wall
   times include descheduling and driver waits; they are not CPU/GPU attribution.

This instrumentation distinguishes capture stages at the real spike. If the
extra time is small while audio breaks, native callback stages are insufficient;
the missing evidence is thread scheduling/driver/presentation/audio execution,
which the parent owns. It would be dishonest to infer a cause from absence of
a long callback alone.

### Optional renderer-supported asynchronous capture: engineering proposal

This is a proposed boundary change, not a proven fix for idle stutter. No
existing Windows LINK export supplies caller-owned async readback, ReadScreen2
or a rendering callback. Those declarations are in the Mupen branch and are
absent from the installed DLL's export table. Calling ReadScreen from an
arbitrary wrapper thread would race the singleton command slot, and a delayed
call can return a later picture with an earlier stamp. Do not implement that.

1. **Establish a renderer baseline before extending it.** Pin the matching
   commit above, its submodules/dependencies and Windows x86 build toolchain.
   Build entrypoints are `projects/msvc/GLideN64.sln` and
   `projects/msvc/GLideN64.vcxproj`, with GLideNUI/libGLideNHQ/osal projects;
   `src/CMakeLists.txt` also exists. Preserve RSPTHREAD, configuration paths,
   graphics defaults, pixel formats and renderer algorithms. Compare an
   unmodified source build to installed LINK using deterministic frame witnesses
   before crediting an extension. Exact original dependency/toolchain identity
   has not been recovered; a matching version string is insufficient. Retain
   upstream licensing/source obligations for any redistributed modified DLL.
2. **Add an optional versioned C extension around the existing UpdateScreen
   transaction.** Capability query must be inert. Negotiate structure size,
   version, pixel/row contract, ownership, producer generation and bounded
   capacity. The Windows wrapper supplies an immutable copy of the pending
   display-list stamp and capture lease/request; do not borrow mutable RAM or
   g_pending storage after returning. Keep the original exported ABI and
   original path for unsupported plugins. Exact symbol names/schema need an
   implementation review with the parent's Python/schema owner.
3. **Preserve the existing capture decision and picture boundary.** End-of-
   ProcessUpdateScreenCommand, immediately after VI_UpdateScreen, is the
   candidate capture point: the same renderer thread owns the current context.
   Carry the existing last-VI-origin decision through this transaction and
   read the same front buffer and status-bar offset as ReadScreen. An every-
   SwapBuffers hook is not equivalent: VI-origin changes can occur without a
   swap, and the existing wrapper deliberately uses that origin rule. DrawOSD
   precedes the renderer swap; an internal render-target copy before OSD/scaling
   would change recorded pixels. Validate all no-swap/multiple-swap cases.
4. **Queue GPU readback into bounded renderer-owned pixel-pack buffers.**
   Issue the same BGR read into a free PBO, insert a fence, preserve all renderer
   GL/cache state, and return without mapping that unsignaled transfer. At
   later renderer command boundaries, check ready fences without waiting and
   copy only completed buffers into owned delivery slots. PBO APIs do not
   promise that issuing glReadPixels never stalls; measure issue time separately
   from fence completion. The existing command architecture also means final
   pending pictures need an explicit pause/RomClosed drain policy.
5. **Keep identity and time attached to the pixels until delivery.** Each
   queued item retains producer generation, capture occurrence, immutable RAM
   stamp, VI origin, dimensions, format and source presentation QPC. Never
   stamp at completion using current RAM, renumber a frame into another
   occurrence, or substitute delivery QPC for source time. Current wrapper
   present_qpc is assigned after blocking ReadScreen; pluginsource uses it as
   video time. Async source-time versus readback-ready-time must therefore be
   explicit and reviewed across stream reader/recorder/PTS/audio consumers;
   claiming the timestamp semantics are automatically unchanged would be false.
   Audio remains on the existing QPC clock, with no emulator audio/input edits.
6. **Specify overload honestly.** Finite memory, no waiting, and retaining
   every offered frame cannot all hold under unbounded GPU delay. Admission
   before capture may reject a new offer with an explicit gap/drop reason;
   accepted completed pictures must retain their original identity. Do not
   discard a picture already promised to the ledger or silently replace it
   with a newer one. If strict every-frame retention is required, stop here and
   resolve that contract before implementation. Existing capture already has
   explicit failures and a bounded ring; this proposal must preserve its
   downstream honesty and avoid introducing unreported shedding.
7. **Verify the changed boundary before proposing installation.** Deterministic
   GL/native host witnesses must compare front-buffer pixels byte-for-byte,
   exact timer/pad stamps, picture occurrence counts, bottom-up BGR/odd edges,
   resize/fullscreen/OSD, reset/ROM generations, zero/two display lists, demand
   expiry, forced out-of-order fence readiness, overflow and shutdown. Include
   negative controls that delay completion by several frames and deliberately
   use current RAM or delivery time; those mutants must fail. Add independently
   decoded visual-frame plus audio-impulse witnesses through the real mux/feed
   chain. A successful PBO mock or a flat callback average alone proves neither
   physical smoothness nor unchanged audio alignment.

OpenGL references opened for this proposal:
[pixel-pack destination semantics](https://registry.khronos.org/OpenGL-Refpages/gl4/html/glReadPixels.xhtml),
[fence wait results and timeout](https://registry.khronos.org/OpenGL-Refpages/gl4/html/glClientWaitSync.xhtml).
These establish API mechanisms, not the performance of this NVIDIA driver.

No new behavioral tests ran during this read-only follow-up. Artifact validation
is `git diff --check`; the previous 31-pass result applies to the earlier native
candidate only. Root owns any later build, test, installation and live proof.

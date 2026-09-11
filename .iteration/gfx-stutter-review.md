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

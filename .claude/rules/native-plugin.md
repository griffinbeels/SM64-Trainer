---
paths:
  - "plugin/gfxwrap/**"
  - "tools/build_plugin.py"
  - "tools/build_renderer.py"
  - "tools/build_natives.py"
  - "tools/stage_link_witness.py"
  - "renderer/**"
---

# Native plugin — where to change what

Three shipped natives: the capture wrapper Project64 selects, the capture
overlay compiled into LINK's GLideN64 v4.2 (the renderer), and the 64-bit
hardware encoder helper. Why they are built this way, and what failed on the way:
[Renderer GPU recording](../../docs/replay-gpu-runtime.md).

| To change... | Edit |
|---|---|
| What Project64 calls, the ini, the About box, the practice ROM gate | `gfxwrap.c`. The practice ROM rule is `practice_rom.h`, mirrored by `core/onboarding.py::is_practice_rom`: change both; `tests/test_practice_rom.py` compares them |
| The input stamp paired with each picture | `stamp_adapter.*`; the value's chain is [chain-input-timeline-frame](chain-input-timeline-frame.md) |
| The renderer overlay (SourceV2/ContextV1, GL state, snapshot, boundary) | `link_dispatch.*`, `link_source_api.*`, `renderer_boundary.*`, `context_lifetime.*`, `gl_snapshot.*`, `renderer_gl_state.h`; `tools/build_renderer.py` `OVERLAY_SOURCES` lists them. Docs: `replay-link-state.md`, `replay-renderer-boundary.md`, `replay-gl-snapshot.md` |
| Native Mario ghost, exact attempt clocks and clean SourceV3 presentation | `ghost_capture.*`, `ghost_clock.*`, `ghost_origin.*`, `ghost_playback.*`, `ghost_draw.*`, `ghost_presentation.*`; worker transport in `ghost_ipc.*`, Python ownership in `ghost/capture.py` and `ghost/native_session.py`. [Runtime contracts](../../docs/ghost-native-runtime.md) own format versions, timing, refusal policy and live verification limits |
| Delivery worker and the renderer-thread GPU steps (selection sample, bridge copy, GL state guard) | `gpu_delivery*`, `gpu_render.*`; doc `replay-gpu-runtime.md#first-capture-freeze`. Every GPU call stays on the renderer's thread: a GL context or D3D11 device on any other Project64 thread brings back the first-capture freeze, and the overlay publishes no shared context. Hardware witnesses build the shipped code through `gpu_render_witness_host.cpp` (renderer) and `gpu_render_witness_consumer.cpp` (helper role) |
| Control page and capture lease | `control.h`, `control_worker.h`, `runtime_control.*`; Python reads the page by offset in `replay/capturecontrol.py`; doc `replay-capture-control.md` |
| Hardware encoding | `gpu_encoder_dll.*` owns shared textures/keys; `gpu_platform_encoder.*` dispatches unchanged NVENC (`gpu_bridge_encoder*`) or Windows D3D11/MF (`gpu_mf_encoder.*`). Python `replay/gpuencoder*.py`, `gpusettings.py` and `gpuprocess/` preserve exact packet identity. Codec/backend/quality consume checked-zero reserved words without moving V1 offsets. Never reinterpret NVIDIA tuning as Windows quality. Real AMD/Intel graphics interop and gameplay qualification are separate from a passing hardware encoder test; see the runtime document. |

- **Build ids hash the sources.** Any edit here changes the wrapper and
  encoder ids (`tools/build_plugin.py`); an overlay edit also changes the
  renderer id (`tools/build_renderer.py`), including a header the wrapper
  shares, such as `gl_snapshot.h`: put wrapper-only behaviour in the `.cpp`
  and the renderer stays byte-identical. The three DLLs are build outputs,
  never committed: `tools/build_natives.py` rebuilds whichever is out of date
  (the launcher, wrap and release run it; a shared cache makes repeats
  instant). Keep LF line endings, since the hash is over bytes. A source
  server (`run-test-server.bat`) refreshes the bundle, never an installed
  layer: install from the setup screen with Project64 closed. Packaged
  builds refresh automatically.
- **Python decodes layouts and codes by position.** The control page,
  `gd_status` and reason enums (`replay/gpudiagnostics.py`), `rb_stats` and
  `rb_surface` keep their offsets and values: append, never renumber. Ghost
  formats and limits are stated once in `ghost/native_layout.py`, checked by
  `tests/test_ghost_layout.py`; the recipe for a new captured field is in
  the runtime contracts doc.
- **The configured gate skips the GPU witnesses.** After a native contract
  change, run them on the NVIDIA machine: `SM64_TEST_GPU_BRIDGE=1`
  (`test_gpu_bridge`, `test_gpu_cadence`, `test_gpu_fidelity`),
  `SM64_TEST_GPU_SELECTION=1` (`test_gpu_selection`), and
  `SM64_LINK_WITNESS_SOURCE=<tree staged by tools/stage_link_witness.py>`
  (`test_link_source_witness`, `test_renderer_source`). Round 48's `SA_NO_SWAP`
  broke the cadence witness silently for exactly this reason: its fake renderer
  never counted swaps. A fake renderer must model everything the stamp adapter
  checks (origin, swap count, epoch), and keep calling UpdateScreen every VI
  between pictures: renderer-thread capture does its GPU work only inside
  those calls.
- **The emulation thread never waits for capture.** Refuse or omit a picture
  instead; a refusal is a counted gap, not a stall or a failed run.

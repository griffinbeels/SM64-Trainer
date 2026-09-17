---
paths:
  - "plugin/gfxwrap/**"
  - "tools/build_plugin.py"
  - "tools/build_renderer.py"
  - "tools/stage_link_witness.py"
  - "renderer/**"
---

# Native plugin — where to change what

Three shipped natives: the capture wrapper Project64 selects, the capture
overlay compiled into LINK's GLideN64 v4.2 (the renderer), and the 64-bit
NVENC helper. Why they are built this way, and what failed on the way:
[Renderer GPU recording](../../docs/replay-gpu-runtime.md).

| To change... | Edit |
|---|---|
| What Project64 calls, the ini, the About box, the practice ROM gate | `gfxwrap.c`. The practice ROM rule is `practice_rom.h`, mirrored by `core/onboarding.py::is_practice_rom`: change both; `tests/test_practice_rom.py` compares them |
| The input stamp paired with each picture | `stamp_adapter.*`; the value's chain is [chain-input-timeline-frame](chain-input-timeline-frame.md) |
| The renderer overlay (SourceV2/ContextV1, GL state, snapshot, boundary) | `link_dispatch.*`, `link_source_api.*`, `renderer_boundary.*`, `context_lifetime.*`, `gl_snapshot.*`, `renderer_gl_state.h`; `tools/build_renderer.py` `OVERLAY_SOURCES` lists them. Docs: `replay-link-state.md`, `replay-renderer-boundary.md`, `replay-gl-snapshot.md` |
| Delivery worker, GPU bridge, selection sample | `gpu_delivery*`, `gpu_bridge*`, `gpu_selection*`; doc `replay-gpu-bridge.md` |
| Control page and capture lease | `control.h`, `control_worker.h`, `runtime_control.*`; Python reads the page by offset in `replay/capturecontrol.py`; doc `replay-capture-control.md` |
| The NVENC helper | `gpu_encoder_dll.*`, `gpu_bridge_encoder*`; Python side `replay/gpuencoder*.py` and `replay/gpuprocess/` |

- **Build ids hash the sources.** Any edit here changes the wrapper and
  encoder ids (`tools/build_plugin.py`); an overlay edit also changes the
  renderer id (`tools/build_renderer.py`). Rebuild and commit the DLLs in
  `src/sm64_events/data/plugin/`; `tests/test_bundled_natives.py` fails until
  you do. Keep LF line endings, since the hash is over bytes. A source server
  (`run-test-server.bat`) never refreshes an installed layer: install from the
  setup screen with Project64 closed. Packaged builds refresh automatically.
- **Python decodes layouts and codes by position.** The control page,
  `gd_status` and reason enums (`replay/gpudiagnostics.py`), `rb_stats` and
  `rb_surface` keep their offsets and values: append, never renumber.
- **The configured gate skips the GPU witnesses.** After a native contract
  change, run them on the NVIDIA machine: `SM64_TEST_GPU_BRIDGE=1`
  (`test_gpu_bridge`, `test_gpu_cadence`, `test_gpu_fidelity`),
  `SM64_TEST_GPU_SELECTION=1` (`test_gpu_selection`), and
  `SM64_LINK_WITNESS_SOURCE=<tree staged by tools/stage_link_witness.py>`
  (`test_link_source_witness`, `test_renderer_source`). Round 48's `SA_NO_SWAP`
  broke the cadence witness silently for exactly this reason: its fake renderer
  never counted swaps. A fake renderer must model everything the stamp adapter
  checks (origin, swap count, epoch).
- **The emulation thread never waits for capture.** Refuse or omit a picture
  instead; a refusal is a counted gap, not a stall or a failed run.

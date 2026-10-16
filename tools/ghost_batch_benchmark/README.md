# Animated multi-ghost batch experiment

## Experiment plan

Use the repository's pinned, hash-verified pose model and a local stock US ROM.
Create one hidden, non-activating WGL drawable, model, atlas, geometry buffers and
batch compositor per resolution. Evaluate and pack genuinely animated Mario
poses every frame. Exercise 1, 4, 16, 32 and 64 poses, both separated and heavily
overlapping, at 640×480 and 1280×960. Shadows are off.

Before timing each case, render every pose individually at full opacity over
black and white. Recover premultiplied RGB and material coverage from those two
images, then independently compose them on the CPU. Compare the resulting
reference against one translucent `begin_batch` call. Separated poses must each
contribute visible pixels. Compare both source color/depth and the published
clean presentation against their original pixels. Verify visible presentation
against the batch image. A last-ghost-only counterfactual must fail the reference
for multi-ghost cases. Keep all readback and reference work outside timing.

Measure model evaluation, vertex packing, compositor submission, presentation,
GPU-completion wait and swap separately, plus their total. Use swap interval zero,
warm-up frames, raw per-frame samples and p50/p95/max. No emulator, recording,
encoding, IPC, live camera or shadow cost is included. Results cannot establish a
live 33.33 ms end-to-end budget. All ROM-derived images and binaries stay private
under the ignored `.iteration/ghost-batch-benchmark` directory.

## Reproduce

Run from this worktree in PowerShell, supplying existing local files:

```powershell
python tools/ghost_batch_benchmark/run.py `
  --rom 'C:\path\to\Super Mario 64 (U) [!].z64' `
  --seed "$env:LOCALAPPDATA\SM64Trainer\build-cache\model" `
  --out .iteration/ghost-batch-benchmark/full
```

Requires Windows, the existing x86 MSVC toolchain, OpenGL 4.3+, and the three
hash-verified local inputs described by `renderer/model/inputs.json`. There are
no downloads, new dependencies, emulator attachments or plugin installations.
The runner only accepts the stock US ROM SHA256 below. It never copies the ROM.
Windows compiler/probe children are hidden and suppress activation feedback.
The WGL window uses `WS_EX_NOACTIVATE`, is never shown and asserts invisibility.
Only paths within this worktree's ignored experiment directory are accepted.

For a short correctness run, add `--samples 3 --warmup 1 --max-count 4
--resolutions 640x480`. The default is 120 samples after 20 warmups for each of
20 cases. Both the model and compositor compile from the worktree; no installed
renderer binary is used. Source hashes are checked before and after the run.

Outputs are `manifest.json`, the model build manifest, compiler logs and, per
resolution, `frames.csv`, `proof.csv`, context identity and private PPM images.
The source reuses the hidden WGL/scene fixture from
`tests/native/ghost_compositor_host.cpp` and the synthetic VI raster pattern from
`tests/native/ghost_pipeline_host.cpp`. Actual production model, compositor and
presentation components are linked without test hooks.

## Observed result — 2026-09-22

All **20 correctness cases and 2,400 measured frames passed**, with 400 warmups.
Host: AMD Ryzen 9 9950X, NVIDIA RTX 5090, OpenGL 4.6.0 driver 616.92. Win32 `/O2`
and `/MT`; verified WGL swap interval 0. This was a shared desktop, not an
exclusive benchmark machine. Evidence is private at
`.iteration/ghost-batch-benchmark/full` in the `ghost-batch-benchmark` worktree.
The private four-panel `contact.png` was visually inspected: all 64 separated
bodies are visible; overlapping bodies accumulate; no shadow is drawn.

Total milliseconds include animation evaluation, geometry packing, all drawing
and presentation submission, `glFinish` completion and hidden `SwapBuffers`.
Each cell is **p50 / p95 / maximum**, measured independently at that count:

| Ghosts | 640×480 separated | 640×480 overlapping | 1280×960 separated | 1280×960 overlapping |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.654 / 1.541 / 2.272 | 0.736 / 1.497 / 2.187 | 0.883 / 1.442 / 2.316 | 0.895 / 1.669 / 2.125 |
| 4 | 1.074 / 1.772 / 2.293 | 1.090 / 1.890 / 2.484 | 1.292 / 2.116 / 2.775 | 1.499 / 2.313 / 3.018 |
| 16 | 2.927 / 4.022 / 4.819 | 3.513 / 5.063 / 5.883 | 2.757 / 3.706 / 4.479 | 3.279 / 4.447 / 5.706 |
| 32 | 4.851 / 6.083 / 7.373 | 5.716 / 6.739 / 8.380 | 4.932 / 6.014 / 6.471 | 6.301 / 7.657 / 8.261 |
| 64 | 9.740 / 12.373 / 15.505 | 10.039 / 12.432 / 14.340 | 10.999 / 12.977 / 15.815 | 11.509 / 13.319 / 13.913 |

No measured synthetic frame exceeded 33.33 ms. This supports continuing a
multi-ghost implementation; **it does not establish the live end-to-end budget**.

The 64-ghost stage costs below are independent p95 values, so they should not be
summed to reconstruct total p95:

| Stage, ms | 640 separated | 640 overlapping | 1280 separated | 1280 overlapping |
| --- | ---: | ---: | ---: | ---: |
| Actual `sg_model_evaluate` | 2.743 | 3.019 | 2.879 | 3.086 |
| Pack `gc::Vertex` arrays | 1.029 | 0.814 | 0.950 | 0.898 |
| `begin_batch` + `finalize` submission | 7.425 | 7.415 | 7.717 | 7.473 |
| Clean VI + presentation submission | 0.309 | 0.278 | 0.286 | 0.318 |
| Entire CPU submission interval | 11.036 | 11.043 | 11.595 | 11.282 |
| Remaining `glFinish` wait | 1.331 | 1.535 | 1.224 | 1.579 |
| Start through GPU completion | 11.406 | 11.737 | 12.097 | 12.208 |
| Hidden swap | 1.223 | 1.111 | 1.228 | 1.270 |

CPU submission includes driver work and possible driver backpressure. The
`glFinish` wait is **not GPU elapsed time**: CPU and GPU already overlap during
submission. No GPU timestamp query was used. Completion is explicitly forced per
frame, so these are latency observations, not unconstrained throughput numbers.

## Correctness and scope

One model/atlas and 64 preallocated mesh/packed buffers are reused. Every frame
evaluates animation `MARIO_ANIM_WALKING` (`0x48`), phases 0–29, distinct yaw and
per-ghost tint. The proof checks N unique geometry hashes within one frame and
different geometry in adjacent frames. Each body contains 2,256 vertices in the
proof; 64 bodies submit 144,384 vertices. The separated grid and the overlapping
layout use different synthetic orthographic transforms; these are not captured
live camera/terrain poses. Opacity is 35%; shadows, gamma and MSAA are off.

All independent composition comparisons had **maximum RGB error 1/255** (allowed
tolerance 4/255 for two calibration readbacks and repeated 8-bit blending), with
zero pixels outside tolerance. Black/white calibration matters: assuming an
opaque Mario material produced 21 false failures in the four-body overlap case
because some atlas pixels have partial alpha. The corrected reference measures
that alpha instead of weakening the comparison. Individual black/white samples
are read back before any batch is drawn, so reference accumulation is independent
of the multi-ghost loop.

Every separated ghost retained visible pixels in the same batch frame. The
smallest 64-body reference silhouette was 315 pixels at 640×480 and 1,279 at
1280×960. Replacing a batch with only its final ghost failed the witness: for
64 ghosts, 24,749/99,041 separated pixels and 56,183/224,599 overlap pixels exceed
tolerance at the two resolutions. This positive control prevents a last-ghost-
only result from being mistaken for success.

Source RGBA and terrain depth stayed exactly unchanged. The published clean
presentation matched the baseline byte for byte, and visible BACK matched the
flipped batch image exactly. These checks run for each case before timing, not
on every measured frame. Screenshot/reference readback and proof allocations are
excluded from measurements; model evaluation, packing and render work are not.

Caller ordering remains whole-body back-to-front. The overlapping reference
proves ordered alpha accumulation, not mathematically exact visibility for
interpenetrating translucent bodies. At 64 overlapping layers, earlier colors
can become indistinguishable at 8-bit precision; the separated case is the proof
that every ghost survives. The performance sweep includes ordinary depth copying
with no obstructing terrain. It does not characterize N64 image depth, live
GLideN64 rendering, input/IPC acquisition, replay decoding, recording/encoding,
OSD, audio, shadows, other GPUs or power states. No live emulator was touched.

Next useful measurement is the integrated, capped multi-ghost playback path
against an active GLideN64 frame and real recording load, with phase-consistent
telemetry and percentile budgets. Do not set a user-facing 64-ghost guarantee
from this synthetic run or extrapolate additional counts from the one-ghost case.

## Exact identity

- Source base: `ff4f3142c3da8daa1a2034c6bbfe5af8628e895f`.
- libsm64 pin: `fd11813208272b4271d92bd92feb8f3fdbe61be5`.
- Stock US ROM SHA256: `17ce077343c6133f8c9f2d6d6d9a4ab62c8cd2aa57c40aea1f490b4c8bb21d91`.
- Model component hash: `00e6cd4a97e04571d2f10fd93ec85dfcb0b70753880e43d30adef1324e920617`.
- Measured executable SHA256: `4d87a349c649e568bd1e9ccb9e8a6c0d4c3acc6f31de1dc7c4eac7eabf123281`.

The private manifest records hashes of both experiment sources and the linked
component sources. Build timestamps can change binary hashes on a rebuild;
source/pin identities and freshly recorded output identify each separate run.
Only reproducible source and this report are committed. ROMs, extracted textures,
screenshots, libraries, executables and raw measurements remain private.

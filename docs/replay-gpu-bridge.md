# GPU delivery bridge

The fixed two-slot `plugin/gfxwrap/gpu_bridge` component is connected through
the [GPU runtime](replay-gpu-runtime.md). It accepts an owned GL snapshot on a dedicated
worker, transfers pixels on the GPU into an ordinary WGL-interoperable D3D11
texture, then into a separate keyed shared texture. The separate x64 witness
consumer opens it on the same adapter, established from the GL and DXGI LUID.
No adapter-index assumption or full-image CPU transfer belongs to the bridge.

Only the worker enters WGL interop locks; a full transport slot refuses before
that lock. WGL may block the worker. The renderer has no explicit dependency
on its completion, but shared-driver/GPU contention still requires live proof.
Failed or abandoned ownership quarantines a fixed process-lifetime pool. Do
not destroy that pool or reconnect on unproved completion. Crash/recovery and
portable readback fallback are not implemented in this component.

`gpu_bridge_encoder` is a synchronous NVENC consumer with one ordinary
registered input texture. It tests real H.264 output, explicit IDR requests,
input/output timestamp metadata and EOS drain. Production calls run inside the
isolated GPU helper; the Python packet mux owns media publication. Failure cannot
reuse unproved GPU custody. The NVIDIA header in `vendor/` retains its license and
pinned provenance. No CUDA Toolkit installation is required.

The test-only raw-pixel oracle checks six distinct images after source overwrite.
A source GPU fence completes while both transport slots remain held and 2000
full-pool capture requests refuse. That establishes progress in this fixture,
not PJ64 pacing under concurrent encoding, OBS or other workloads. Independent
H.264 decoding identifies all six markers; opaque NVENC timestamp metadata is
not a muxed VFR/audio/fragment synchronization proof. Logical texture bytes are
not measured driver/NVENC VRAM usage.

The original raw bridge witness preserved GL row order. The connected worker
now applies the exact transfer convention below, including odd-height cropping:
retain rows 1..H-1 before flipping an odd-height source, not rows 0..H-2.
Existing FFmpeg conversion remains the color reference, not just its codec
preset label. Source state, leases, media and input identity are separate
connected-runtime responsibilities; this component alone cannot certify them.
Live pacing and AMD/Intel routes remain unverified.

Run the actual hardware witness explicitly in the worktree:

```powershell
$env:SM64_TEST_GPU_BRIDGE='1'
.venv/Scripts/python.exe tools/run_tests.py tests/test_gpu_bridge.py
```

Without the flag it explicitly skips. Positive small/full-size paths and an
omitted-copy negative control use hidden x86/x64 child processes only. No live
server, PJ64 process, installed plugin or graphics setting is modified.

API references: [WGL interop2](https://registry.khronos.org/OpenGL/extensions/NV/WGL_NV_DX_interop2.txt),
[DXGI keyed ownership](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgikeyedmutex-acquiresync),
[NVENC guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-video-encoder-api-prog-guide/index.html).

## Exact worker transfer convention

The worker now uses one prepared shader draw to combine top-down orientation,
floor-even top-left cropping and opaque alpha, matching pixels.py plus the
existing sink crop. Odd source height drops native GL row0; odd width drops the
rightmost column. No CPU image conversion is used. The dedicated worker owns
its GL state and initializes shader/VAO/sampler/FBO resources once. Its normal
shutdown unbinds/deletes them; a fault retains the bounded quarantined pool.

The 640x480 and 641x481 independent raw pixel fixtures match every RGBA byte.
Removing flip, changing the odd row or swapping red/blue fails those oracles.
Existing cross-process and NVENC marker witnesses now expect top-down output.
Color signaling, encoder settings, live driver contention and emulation pacing
remain separate gates; raw transfer fidelity alone does not establish them.

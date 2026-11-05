# GPU delivery bridge

The bridge carries a selected picture from the renderer's GL context into the
encoder helper's D3D11 texture. Since 2026-09-27 it is `render_capture::Bridge`
in `plugin/gfxwrap/gpu_render.cpp`, run on the renderer's own thread
([renderer-thread capture](replay-gpu-runtime.md#first-capture-freeze)).

The helper creates two textures under the channel's names
(`gpu_transport_helper.h`, used by `gpu_encoder_dll.cpp` and every helper-role
witness). The renderer writes each selected picture as the floor-even top-left
crop, top row first: an odd height drops native row 0, an odd width the
rightmost column. Alpha passes through; the encoder does not read it. Project64
makes no D3D11 device and no second GL context; the adapter is the GL
context's own (`GL_DEVICE_LUID_EXT`), which the helper's device must match.

## Transports

How the picture crosses and how custody comes back is the
[bridge transport](glossary.md#bridge-transport) (`gpu_transport.h`, wire
values 1-3). The renderer probes its context once per request, in that
request's first renderer tick and before its surface is checked, and selects
the first transport the context offers in this order:

| Transport | Needs (besides GL 4.5 and `GL_EXT_memory_object(_win32)`) | Copy | Custody |
|---|---|---|---|
| 1 keyed mutex | `GL_EXT_win32_keyed_mutex` | blit into the imported texture | key 0 / key 1 (below) |
| 2 semaphore | `GL_EXT_semaphore`, `GL_EXT_semaphore_win32` | blit into the imported texture | a shared D3D11 fence per texture, plus the section's words |
| 3 readback | nothing more | pack-buffer readback, then a CPU copy into a shared section | the section's words |

Every transport needs `GL_EXT_memory_object_win32` because the helper's
adapter comes from its `DEVICE_LUID_EXT`; without it the context has no
transport and keeps the existing 10029/10030 verdicts. NVIDIA always selects
the keyed mutex, unchanged. The channel header (version 4) and the helper's
Open (`gbdll_config_v1.transport`) carry the selection; both helpers, clean and
ghost copy, create their textures for it.

**Keyed mutex.** The helper's textures are keyed-mutex shared textures,
imported with `GL_EXT_memory_object_win32` and written by one blit between
acquiring key 0 and releasing key 1. On NVIDIA a zero-timeout
`glAcquireKeyedMutexWin32EXT` returns TRUE even when it timed out (another
process holds the key); the following release then raises
`GL_INVALID_OPERATION`, which the key-return poll reads as "not yet" (11-30 us
per poll; a scratch probe of 48,000 polls against a key another process held
never read one as returned).

**Semaphore.** The helper creates ordinary NT-handle shared textures and, for
each, a shared fence (`ID3D11Device5::CreateFence` with
`D3D11_FENCE_FLAG_SHARED`, named `<texture>-fence`). The renderer imports the
fence by name as a GL semaphore with `HANDLE_TYPE_D3D12_FENCE_EXT` (0x9594;
import by name is defined for that type in
[EXT_external_objects_win32](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_external_objects_win32.txt)),
setting each value through `D3D12_FENCE_VALUE_EXT` (0x9595). The renderer
signals odd values after its blit (the signal flushes the GL command stream);
the helper's immediate context `Wait`s for that value before it reads and
`Signal`s the next even value behind its read. The renderer never waits on the
CPU: it learns the return from the section's `returned` word, which the helper
writes once its read completed, and only then queues a GPU-side
`glWaitSemaphoreEXT` for that already-reached value before rewriting the
texture.

**Readback.** The helper creates an unshared texture and a section,
`<texture>-section`, holding a 64-byte header and the picture. `copy()` reads
the crop into a pixel-pack buffer (`glGetTextureSubImage`, no wait) and places
a fence; on a later renderer tick the zero-time fence poll succeeds, the
renderer maps the buffer and copies the rows into the section top row first,
then advances `written`. The delivery publishes the bridge only after that, so
the picture's metadata never reaches the helper before its pixels. The helper
uploads the section with `UpdateSubresource` and encodes as usual. Cost: one
CPU copy of the picture on the renderer thread per selected picture (7.7 MB at
1600x1200). The 1600x1200 cadence witness passes with it on NVIDIA; its
renderer time is not yet measured, on NVIDIA or AMD.

**Sections.** Semaphore and readback custody is a CPU-visible record, not the
channel: the renderer writes `written`, the helper `returned` (64-bit
interlocked words, `gt_section`). The channel's receipt still only acknowledges
metadata. A texture is written only when `returned` shows the helper gave the
last picture back; otherwise the copy answers `full` and the delivery's credit
ledger waits, as with a held key.

**Demotion.** When the driver refuses a semaphore or readback import (a GL
error from `Bridge::prepare`), that request fails `GD_BRIDGE_FAILED` with the
existing retry, and this Project64 never selects that transport again (the
`floor` in the receipt). When none is left, the next request fails before any
channel with 10036 ("every GPU recording bridge this graphics driver offers
failed"), which the trainer treats like the 10028-10033 verdicts: the desktop
grab for that Project64. A missing name (a dead helper) demotes nothing. The
keyed mutex keeps its existing retry and never demotes.

A faulted or abandoned key or section quarantines the process's capture
instead of reallocating.

## The ghost copy

The ghost copy ([runtime](replay-gpu-runtime.md#the-ghost-copy)) imports a
second pair, `Local\SM64GpuImage-<nonce>-<epoch>-copy-0/1`, created by its own
helper, into a second `Bridge` with the same transport, crop, flip and ledger.
It records through the keyed mutex and the semaphore. It declines the readback
with `GD_TWIN_TRANSPORT_UNSUPPORTED` (10037, twin status only): its pictures
would publish a tick after their copy and add a second CPU copy per picture.
The clean recording continues.

Its faults end only the twin: a faulted copy or a key held past the picture
deadline is never waited for or retried; that pair's imports are kept until
the epoch's GL retirement and no other lane starts in that capture run.
Measured (NVIDIA, 2026-10-03): a helper killed while holding key 1 leaves an
abandoned mutex whose key 0 the next poll gets back, so a killed twin helper is
a clean stop (~0.2 s); only a live helper that never returns its key reaches
the deadline. A killed semaphore helper never writes `returned`, so its texture
waits for that deadline instead.

## Hardware witnesses

Each builds the shipped code: `gpu_render_witness_host.cpp` is an x86
renderer with every capture step in its one GL context (the snapshot pool in
its one-context mode, then the sampler or the bridge), and
`gpu_render_witness_consumer.cpp` is the x64 helper, which creates the
textures with the shipped helper's transport code, takes each picture, checks
or dumps the pixels and hands it back (`tests/gpu_render_witness.py` builds and
runs the pair). The host leaves the renderer's scissor, sRGB writes, blending,
depth/stencil/cull, colour mask and pack state hostile around every capture
call and checks they come back.

`SM64_TEST_GPU_TRANSPORT=keyed|semaphore|readback` makes a test host select
that transport (it starts the order there, so demotion still runs). Only test
builds read it: `GD_TEST_HOST` or `GD_TRANSPORT_TEST`, which the shipped
wrapper never defines (`test_gpu_transport.py` pins both). Each witness below
runs every transport on this machine's NVIDIA GPU.

- `test_gpu_bridge.py` (`SM64_TEST_GPU_BRIDGE=1`): six pictures through
  cross-process custody, every pixel checked against the fixture formula at an
  odd 9x7 snapshot cropped from (1,2); a held texture refused as `full`
  (semaphore, readback); an omitted-copy negative control; and 320x240 through
  the shipped NVENC encoder in H.264 and AV1, muxed by `PacketFragmentMux`
  before any decode, with the sample entry, PTS, IDR pattern and decoded
  markers checked.
- `test_gpu_fidelity.py` (`SM64_TEST_GPU_BRIDGE=1`): exact RGB at 640x480 and
  641x481 against an independent bottom-up reference, with the flip and
  odd-row negative controls (`RENDER_FIDELITY_*`, which the readback's CPU copy
  honours too).
- `test_gpu_selection.py` (`SM64_TEST_GPU_SELECTION=1`): the stride-8 sample
  equals the full-frame selector's own sample byte for byte at 640x480 and
  641x481, and the selector makes the same decisions from either; wrong-row,
  crop-first and byte-order negative controls (`RENDER_SAMPLE_*`).
- `test_gpu_delivery.py` and `test_gpu_cadence.py` cover the bridge inside the
  delivery: 17x11 pixels and custody through stop and restart, the renderer's
  GL state, a refused import demoting semaphore to readback to 10036, and the
  real helper at 60 VI / 30 pictures (320x240 and 1600x1200).
- `test_ghost_copy_native.py`: the ghost copy over the keyed mutex and the
  semaphore, and the readback's decline.
- `test_gpu_transport.py`: the selection rule over capability sets, the
  receipt's decoding and privacy, and the override's guard.

Measured 2026-10-31 on NVIDIA (RTX 5090): the context offers every capability
(`0x8000007f`, including `WGL_NV_DX_interop2`), and all three transports pass
every witness above, so a D3D11 fence imported as a GL semaphore works there.

**Unproven until an AMD tester runs it.** No AMD OpenGL is reachable here
(this hybrid's AMD outputs have no display). The field report (RX 9070 XT,
driver 26.8.1) showed the AMD context passing GL 4.5 and both memory-object
extensions and lacking only the keyed mutex, so it should select the
semaphore. Whether that driver exports `GL_EXT_semaphore_win32`, accepts a
D3D11 fence by name, and orders the AMD Media Foundation encoder's read behind
the D3D11 `Wait`, and what the readback costs its renderer, are open until his
next Debug report (its startup receipt now names the capabilities and the
selected transport) and a recording he plays back.

No live server, Project64 process, installed plugin or graphics setting is
touched. The retired worker-thread producer (`gpu_bridge.cpp`, a WGL/D3D11
interop pool, and its `gpu_selection.cpp` shader) was deleted on 2026-09-28
with the witnesses that only built it.

API references: [external memory objects and semaphores](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_external_objects_win32.txt),
[GL semaphores and layouts](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_external_objects.txt),
[GL keyed mutex](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_win32_keyed_mutex.txt),
[DXGI keyed ownership](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgikeyedmutex-acquiresync),
[D3D11 shared fences](https://learn.microsoft.com/en-us/windows/win32/api/d3d11_4/nf-d3d11_4-id3d11device5-createfence),
[NVENC guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-video-encoder-api-prog-guide/index.html).

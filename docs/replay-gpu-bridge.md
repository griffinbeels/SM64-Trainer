# GPU delivery bridge

The bridge carries a selected picture from the renderer's GL context into the
encoder helper's D3D11 texture. Since 2026-09-27 it is `render_capture::Bridge`
in `plugin/gfxwrap/gpu_render.cpp`, run on the renderer's own thread
([renderer-thread capture](replay-gpu-runtime.md#first-capture-freeze)).

The helper creates two keyed-mutex D3D11 textures under the channel's names
(`gpu_encoder_dll.cpp`, open-else-create). The renderer imports them with
`GL_EXT_memory_object_win32` and `GL_EXT_win32_keyed_mutex` and writes each
selected picture with one nearest blit that flips it top row first and takes
the floor-even top-left crop: an odd height drops native row 0, an odd width
the rightmost column. Alpha passes through; the encoder does not read it.
Project64 makes no D3D11 device and no second GL context; the adapter is the
GL context's own (`GL_DEVICE_LUID_EXT`), which the helper's device must match.

Custody is the delivery's credit ledger: a texture is written only after its
key 0 came back. On NVIDIA a zero-timeout `glAcquireKeyedMutexWin32EXT`
returns TRUE even when it timed out (another process holds the key); the
following release then raises `GL_INVALID_OPERATION`, which the key-return
poll reads as "not yet" (11-30 us per poll; a scratch probe of 48,000 polls
against a key another process held never read one as returned). A faulted or
abandoned key quarantines the process's capture instead of reallocating.

## Hardware witnesses

Each builds the shipped code: `gpu_render_witness_host.cpp` is an x86
renderer with every capture step in its one GL context (the snapshot pool in
its one-context mode, then the sampler or the bridge), and
`gpu_render_witness_consumer.cpp` is the x64 helper, which creates the
textures, takes key 1, checks or dumps the pixels and returns key 0
(`tests/gpu_render_witness.py` builds and runs the pair). The host leaves the
renderer's scissor, sRGB writes, blending, depth/stencil/cull, colour mask and
pack state hostile around every capture call and checks they come back.

- `test_gpu_bridge.py` (`SM64_TEST_GPU_BRIDGE=1`): six pictures through
  cross-process custody, every pixel checked against the fixture formula at an
  odd 9x7 snapshot cropped from (1,2); an omitted-copy negative control; and
  320x240 through the shipped NVENC encoder in H.264 and AV1, muxed by
  `PacketFragmentMux` before any decode, with the sample entry, PTS, IDR
  pattern and decoded markers checked.
- `test_gpu_fidelity.py` (`SM64_TEST_GPU_BRIDGE=1`): exact RGB at 640x480 and
  641x481 against an independent bottom-up reference, with the flip and
  odd-row negative controls (`RENDER_FIDELITY_*`).
- `test_gpu_selection.py` (`SM64_TEST_GPU_SELECTION=1`): the stride-8 sample
  equals the full-frame selector's own sample byte for byte at 640x480 and
  641x481, and the selector makes the same decisions from either; wrong-row,
  crop-first and byte-order negative controls (`RENDER_SAMPLE_*`).
- `test_gpu_delivery.py` and `test_gpu_cadence.py` cover the bridge inside the
  delivery: 17x11 pixels and key custody through stop and restart, and the real
  helper at 60 VI / 30 pictures.

No live server, Project64 process, installed plugin or graphics setting is
touched. The retired worker-thread producer (`gpu_bridge.cpp`, a WGL/D3D11
interop pool, and its `gpu_selection.cpp` shader) was deleted on 2026-09-28
with the witnesses that only built it.

API references: [external memory objects](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_external_objects_win32.txt),
[GL keyed mutex](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_win32_keyed_mutex.txt),
[DXGI keyed ownership](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgikeyedmutex-acquiresync),
[NVENC guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/nvenc-video-encoder-api-prog-guide/index.html).

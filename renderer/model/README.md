# Native Mario pose model

`model.h` is the only renderer-facing header. The component evaluates recorded
visual state with libsm64's Mario graph, animation tables and trigonometry. It
does not simulate inputs, query terrain, advance animation, own an OpenGL
context, load files, or draw. The renderer owns ROM identity validation, camera
projection, GPU resources, depth testing, ghost opacity and scheduling.

Open one runtime on its owner thread with validated, big-endian US ROM bytes.
Upload the borrowed RGBA atlas before closing the runtime. Call evaluate with
the captured gfx transform, animation ID/frame and body state; `body_action`
is distinct from `action`. Effective head rotation is supplied after the
original camera/action rules. A supplied throw matrix preserves terrain
alignment and replaces the position/angle transform. `sg_model_view` uses the
same sine table and SM64 look-at routine; its raw Mat4 translation is at
indices 12–14. The renderer supplies the live projection separately. The current
adapter uses floating-point model/view transforms; the game's per-node N64
fixed-point matrix quantization is not reproduced. Pixel-identical registration
therefore remains unproven, separately from model/opacity/depth correctness.

All calls are serialized with a nonblocking guard. Wrong-thread use fails;
there is no worker, lock wait or per-evaluation heap allocation. A preload
worker can open the runtime, detach it, and publish its handle for the renderer
to adopt. Only the current owner may detach; only a detached runtime may be
adopted. Both operations are bounded and preserve ownership on failure. A
detached runtime refuses atlas access, view/evaluation and close. The caller
owns synchronized publication and retirement; no timeout or thread exit
transfers ownership automatically. Close on the current owner thread after
consumers stop using the atlas. Handles and borrowed
pointers expire at close and must not survive a later open. Failures clear the
mesh's vertex count; the caller must discard partial buffer contents. Maximum
capacity is 3072 vertices. Scratch storage is reused and checked before each
allocation; exhaustion returns an error through a C-only recovery boundary.

Asset loading checks MIO0 streams/back-references, decoded size, directory
count, animation offsets, skeleton channel indices and allocation budgets.
This establishes memory bounds, not ROM provenance or arbitrary ROM support.
The caller must separately establish its expected ROM/version identity.

## Build and verification

`tools/build_model.py` builds an x86 MSVC `/MT` static library in caller-owned
staging. `component_hash()` covers the recipe, owned files and pinned hashes in
`inputs.json`. Downloads are checked before extraction/import. No ROM is
downloaded or copied into the source tree. The recipe compiles only graph,
animation traversal, math, geometry adaptation and necessary transform
helpers, plus SM64's audio engine for ghost sounds behind `audio.h` (an owned
loader replacing libsm64's leaking one; see its header for the region
contract); input simulation, collision, filesystem utilities and image
encoders are excluded. The original libsm64 global initializer is not called.

Set `SM64_MODEL_ROM` to an explicitly supplied local ROM and optionally
`SM64_MODEL_SEED_DIR` to cached pinned inputs, then run
`python tools/run_tests.py tests/test_native_model.py`. The native witness
also runs against `SM64_MODEL_PRACTICE_ROM` when supplied and records both
asset hashes; this tests the practice ROM's actual asset sections.
It compiles against the public header and exercises malformed assets, all animation
endpoints, repeated/interleaved poses, distinct body/head transforms, normals,
capacity and scratch failures, camera matrices, lifecycle and thread checks.
Its WGL window stays hidden and renders to an FBO. That readback establishes
model-to-GL geometry delivery, not the live renderer's camera/depth alignment,
frame timing or user-visible quality. `testing=True` adds fault injection and
must not be used in a delivered renderer.

## Current fidelity boundary and provenance

Colors and UVs follow libsm64's atlas convention. Normals use inverse transpose
under nonuniform scale. High-detail Mario is used regardless of camera range.
The per-vertex material channel comes from the active display-list combiner,
with all three vertices of each triangle agreeing. Solid triangles use vertex
color (their UV convention remains `(1,1)`). Body/feature
textures blend atlas RGB with vertex color using atlas alpha; alpha zero does
not punch holes in the face or hat. Wing tiles 9 and 10 use texture-alpha
cutouts. These differ in the imported display lists: `G_CC_BLENDRGBFADEA` for
face features and `G_CC_DECALRGBA` for wings. Select behavior using the material
channel rather than inferring a combiner from UVs. The API transports the
supported combiner behavior, not the full N64 material command stream.
The model does not produce particle effects, a shadow, scene lighting, or
exact N64 metal reflections. The atlas holds Mario's 11 upstream textures, the
metal wing and wing tip (cells 11, 12), then held objects' textures (13 on);
it lacks directional-eye variants, and unsupported states fail closed instead
of indexing outside the atlas.

Held objects (`SG_POSE_HELD`, `held.c`: the Bob-omb, the small penguin, King
Bob-omb, Chuckya, the small breakable box and Mips) are compiled from their upstream geo, lists and
animations like Mario (`HELD_MODELS` in `tools/build_model.py`). The build
walks each geo's display lists for the RGBA16 textures it reaches and
generates `held_cells.h`: the kinds, each texture's atlas cell and size, and
its ROM segment (the model's MIO0 block, the same in the US and Usamune US
ROMs), which assets.c decodes. Adding a kind is a HELD_MODELS row, its pinned
inputs, its sg_held_kind and its first atlas cell in model.h, and its
behaviour in ghost_pose.cpp. Each graph runs
under a wrapper like `mario_geo_libsm64`: libsm64 emits display lists only
from a z-buffered master list, draws nothing under a shadow node (so a held
graph's `GEO_SHADOW` becomes a start node), and drops the first opaque
master-list entry (an empty list takes it). The graph evaluates in the
caller's view so billboards face that camera, then returns to world space.
A unit scale under the wrapper publishes the root's fixed-point matrix, which
a graph whose lists hang off its root (the box) draws under. A kind without
animations (the box) takes animation id -1. A kind whose textures failed to
load is refused, never drawn untextured.

Each textured triangle's material word carries its render tile above the
combiner code (`sg_tile`, from bit `SG_MATERIAL_TILE_SHIFT`): the texture's
atlas cell, and per axis whether it clamps, its wrap mask and its size, as
`gsDPSetTile` and `gsDPSetTileSize` set them. The ghost compositor clamps and
wraps each texel inside the cell as the RDP does. Most of Mips' coordinates run
past its 32x32 fur under G_TX_CLAMP, King Bob-omb's 16-texel tile wraps across,
and 58 of Mario's 150 textured vertices (his button, logo, sideburns, mustache
and eyes) sit just past their clamped 32x32 textures; without the tile they
sampled the neighbouring atlas cells. Mirroring, shifts and non-power-of-two
tiles are refused (`SG_MESH_UNSUPPORTED_MATERIAL`). The metal environment map
(texgen) carries no tile: its coordinates are generated in range. Vanish/ghost opacity is a
renderer concern. Replaying geometry alone does not reproduce the entire
game's material pipeline.

`inputs.json` pins libsm64 revision
`fd11813208272b4271d92bd92feb8f3fdbe61be5` and the Mario geometry/model source
imported from n64decomp/sm64 revision
`06ec56df7f951f88da05f468cdcacecba496145a`, with exact primary URLs and SHA-256
hashes. libsm64's archive supplies `LICENSE.md` (CC0-1.0), preserved in staging.
That license label does not characterize separately imported decomp model
source or user-supplied ROM textures/animations. No ROM assets are vendored.

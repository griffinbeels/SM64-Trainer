# The graphics plugin: LINK's GLideN64 v4.2 with the Practice Replay capture

The graphics plugin the trainer installs into Project64 is ONE file,
`sm64_trainer_gfx.dll`. It is LINK's GLideN64 v4.2 (the renderer) with two
additions built from `plugin/gfxwrap/`: the capture overlay compiled into the
renderer (the SourceV2/ContextV1 boundary, the GPU snapshot, the ghosts), and
the capture layer that exports the Zilmar functions Project64 calls and
forwards each one to the renderer's own (`gfxwrap.c`, its runtime, and the
seam `renderer_entries.h`).

Until 2026-11-07 these were two files: a capture wrapper Project64 selected,
which loaded the renderer by a path its `sm64_trainer_gfx.ini` named, the
renderer installed as `sm64_trainer_gfx.renderer` so Project64 would not list
it. Defender flagged that renderer (Trojan:Win32/Bearfoos.A!ml, 2026-11-06),
and a proxy DLL forwarding to a disguised sibling is the shape malware takes.
The plugin now loads no other plugin and reads no ini of its own; GLideN64's
own `GLideN64.ini` beside it is its only settings file, as for a stock
GLideN64.

## Why a source build of this renderer

LINK's GLideN64 v4.2 is the renderer the Project64 1.6 install it targets
already ran (original DLL SHA-256 `49f384e8c62f61ff8555d5055654df7f0cdbe81497cd85e685e3a508d7dffe83`),
so picture, smoothness and settings stay as players know them. Stock GLideN64
offers only a synchronous CPU `ReadScreen`, which makes emulation wait, and an
earlier adapter that patched that exact binary depended on its bytes and
private offsets. Building from the pinned source lets the overlay take a GPU
copy of each finished frame with its input stamp and never make the game wait.
The rebuilt baseline passed a side-by-side check against the stock DLL ("same
picture, smoothness and clean audio"). Full rationale:
[`docs/replay-gpu-runtime.md`](../docs/replay-gpu-runtime.md).

## Credit

The renderer is [LINK's GLideN64 v4.2](https://github.com/Luna-Project64/GLideN64),
the Luna-Project64 fork of [GLideN64](https://github.com/gonetz/GLideN64) by
Sergey Lipskiy, with Olivieryuyu, Ryan Rosser and the GLideN64 contributors,
which itself credits Orkin (glN64), yongzh (gles2n64), Hiroshi Morii (GlideHQ)
and ziggy (z64). These are the credits the pinned source names in its own
About dialog. griffman1212 built the capture overlay and the capture layer on
top of it.

GLideN64 is licensed under the GNU GPL v2 (`LICENSE` in the upstream tree),
so this modified build is too. The complete corresponding source is:

- the upstream tree at commit `d0d1010`, pinned by archive and tree hash in
  `build-inputs.json`;
- the overlay in `plugin/gfxwrap/` (`link_dispatch.*`, `renderer_boundary.*`,
  `link_source_api.*`, `context_lifetime.*`, `renderer_gl_state.h`,
  `gl_snapshot.h`, `source_format.h`, `practice_rom.h`, `ghost_*`,
  `renderer_settings_folder.*`; `tools/build_renderer.py` `OVERLAY_SOURCES`
  and `OVERLAY_MODULES` list them) and the edits `tools/stage_link_witness.py`,
  `tools/stage_ghost_renderer.py` and `tools/stage_ghost_presentation.py` make
  to the upstream files, with the pose model in `renderer/model/`
  (`tools/build_model.py`);
- the capture layer in `plugin/gfxwrap/`: `gfxwrap.c`, `gfxwrap.def`,
  `renderer_entries.*`, the units `tools/build_plugin.py` `RUNTIME_SOURCES`
  lists (`wrapper_runtime`, `stamp_adapter`, `runtime_control`,
  `runtime_delivery`, `gpu_request`, `gpu_delivery`, `gpu_render`,
  `source_snapshot`, `gl_snapshot`, `gpu_channel`, `scene_tap`,
  `scene_spool`), and every header in that folder they include;
- the project adaptations and build steps in `tools/build_renderer.py`:
  the one edit that lets a scene-lab host name the renderer's settings folder
  (`PluginAPI::FindPluginPath` asks `renderer_settings_folder::named()`
  first, and inside Project64 nothing names one), and the renaming of
  upstream's Zilmar functions to `GLideN64_<name>` and its `DllMain` to
  `GLideN64_DllMain` (`stage_renderer_entries`), so the capture layer
  exports those names and calls the renderer's; their bodies are unchanged.

Project64 shows the plugin's own About box (the credits and the build id).
The renderer's About dialog is not reachable from Project64.

## Building

    uv run python tools/build_natives.py      # the plugin and the encoder helper, only what is out of date
    uv run python tools/build_renderer.py     # the plugin, always

The tool downloads the pinned upstream archive into the build cache
(`%LOCALAPPDATA%\SM64Trainer\build-cache\renderer`), verifies it, stages the
overlay and the capture layer in a scratch folder, builds Release/Win32 with
MSBuild (x86 MSVC, static CRT) and writes
`src/sm64_events/data/plugin/sm64_trainer_gfx.dll` and nothing else. Each
capture-layer unit compiles with the settings the separate wrapper had
(`tools/build_renderer.py` `_capture_item`), outside the renderer's
whole-program optimization. The DLL carries ONE build id (a digest of every
input above, ending `-gpu-runtime`), which is how the setup screen decides
whether an installed plugin is this build's and how
`tests/test_bundled_natives.py` checks the bundled file matches the tree.
The DLL is a build output that git ignores; `tools/build_natives.py` keeps
built copies by id in `%LOCALAPPDATA%\SM64Trainer\build-cache\natives`.

The host tests never need MSBuild: `tools/build_plugin.py`
`build_test_plugin` links the same capture layer with a stand-in renderer
into a test `sm64_trainer_gfx.dll` that never enters the bundle.

### The three prebuilt libraries

The upstream project links three static libraries it builds itself:
`osal.lib`, `libGLideNHQ.lib` and `GLideNUI.lib` (the settings dialog, which
needs a static Qt 5.15.2). The tool takes them from the build cache and
refuses any file whose hash is not the one in `build-inputs.json`. They are
not in git (`GLideNUI.lib` alone is 85 MB).

They were produced once (2026-09) from the same pinned tree with MSVC 14.51,
Windows SDK 10.0.26100, `/MT`, and a Qt 5.15.2 built from the official
`qtbase-everywhere-src-5.15.2.tar.xz` with:

    configure -static -static-runtime -release -opensource -confirm-license
              -platform win32-msvc -nomake examples -nomake tests
              -opengl desktop -no-icu -no-openssl -no-dbus -no-freetype

`-no-freetype` keeps the renderer's own FreeType 2.5.3; Qt's newer backend
needed a symbol that library lacks. Only QtBase and jom were required.
`GLideNUI.vcxproj` was pointed at that Qt prefix and built as a static
library that merges the Qt libraries it links, which is why the final DLL
has no Qt or VC runtime DLL dependency. Rebuilding them is only needed when
the pinned upstream tree or the Qt version changes; the pin in
`build-inputs.json` changes with them.

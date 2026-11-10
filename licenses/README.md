# Third-party notices

SM64 Trainer is free software under the GNU GPL v3 or later (`LICENSE` at
the repository root, and `SM64-Trainer/LICENSE` in an installed copy). Its
source is <https://github.com/griffinbeels/SM64-Trainer>; each release is a
tag there.

Two parts the app carries are GPL v2 only: LINK's GLideN64, in the graphics
plugin, and Unicorn, the CPU emulator the Ghost Lab runs the game's own code
in (`python/unicorn/README.md`). GPL v3 code cannot be combined with GPL
v2-only code; "v2 or later" combines with both. So the trainer's own code
that is built into the graphics plugin, that loads the plugin or the
plugin's code into its own process, or that runs in Unicorn's process, is
under the GNU GPL v2 or later instead (`renderer/GPL-2.0.txt` is version 2).
That code is:

- `plugin/gfxwrap/`, as its `LICENSE.md` says (not `vendor/` or `zilmar.h`);
- `renderer/model/`, the pose model linked into the plugin;
- the tools that build the plugin and write its edits into GLideN64's files:
  `tools/build_renderer.py`, `build_renderer_libs.py` (with the patches in
  `renderer/patches/`), `build_plugin.py`, `build_model.py`,
  `native_identity.py`, `native_build_cache.py`, `stage_link_witness.py`,
  `stage_ghost_renderer.py` and `stage_ghost_presentation.py`;
- the scene-lab hosts that load the plugin: `tools/scene_lab/viewer_host.c`,
  `replay_host.c` and `renderer_load.h`;
- the Ghost Lab hosts built from the plugin's code:
  `tools/scene_lab/tap_offline_host.cpp` (with the plugin's scene tap,
  `scene_tap.cpp` and `scene_spool.cpp`) and `ghost_sound_host.cpp` (with
  its ghost sound, `ghost_audio.cpp` and `ghost_assets.cpp`);
- the Ghost Lab's native hooks inside Unicorn's process:
  `tools/scene_lab/cpu_hooks.c`;
- the Ghost Lab's simulation program, `SM64TrainerSim.exe` (its entry
  `sim_entry.py`), and every trainer module it loads, each marked
  `SPDX-License-Identifier: GPL-2.0-or-later`
  (`tools/scene_lab/sim_boundary.py` MODULES is the list): in
  `tools/scene_lab/`, `audio_clock.py`, `audio_layer.py`,
  `audio_session.py`, `camera_state.py`, `cold_boot.py`, `cpu_hooks.py`,
  `e2_georender.py`, `evaluator.py`, `evaluator_child.py`, `frame_sim.py`,
  `ghost_resim.py`, `handoff_slot.py`, `live_spool.py`, `moment_slot.py`,
  `package.py`, `pick.py`, `provenance.py`, `savestate_frames.py`,
  `sim_boundary.py`, `sim_scene.py`, `slot_atlas.py`, `stage_cache.py`,
  `stage_sim.py`, `start_check.py`, `state_resolver.py`, `stems_check.py`,
  `stems_layer.py`, `stems_probe.py`, `tap.py`; in `src/sm64_events/`,
  `__init__.py`, `core/__init__.py`, `core/build_channel.py`,
  `core/childproc.py`, `core/events.py`, `core/lab_inputs.py`,
  `core/paths.py`, `core/snapshot.py`, `core/update_plan.py`,
  `core/version.py`, `detectors/__init__.py`, `detectors/anchors.py`,
  `detectors/counter_epoch.py`, `ghost/__init__.py`, `ghost/clip.py`,
  `ghost/native_layout.py`, `ghost/start_frame.py`, `inputs/__init__.py`,
  `inputs/frame.py`, `memory/__init__.py`, `memory/addresses.py`,
  `memory/base.py`, `memory/behaviours.py`, `memory/ghost_layout.py`,
  `memory/layout.py`, `memory/objects.py`, `memory/version_probe.py`,
  `scenelab/__init__.py`, `scenelab/probe.py`, `scenelab/roms.py`,
  `scenelab_code.py`, `slotlibrary/__init__.py`, `slotlibrary/atlas.py`,
  `slotlibrary/relocate.py`, `slotlibrary/store.py`,
  `slotlibrary/usamune.py`.

The plugin as one file follows GLideN64's license, GPL v2, and Unicorn
follows its own, GPL v2 only. Unicorn must only run in a process that loads
no GPL v3 or LGPL v3 library, such as PyAV's FFmpeg or pystray.

This folder ships with the app as `_internal/licenses/`
(`tools/third_party_notices.py` stages it at build time). It holds the
license of everything the app carries that someone else made, and, for the
GPL and LGPL parts, where their complete source is. The Credits list in
About (`src/sm64_events/ui/credits.js`) says who made each one.

The complete source of every third-party GPL and LGPL part is attached to
each release, on its page on GitHub, beside the installer:

- `SM64Trainer-third-party-source-plugin.tar`: the sources the graphics
  plugin is built from (the pinned GLideN64 tree, Qt, FreeType, libpng,
  zlib);
- `SM64Trainer-third-party-source-ffmpeg.tar`: the sources of `ffmpeg.exe`
  and of PyAV's FFmpeg libraries, with the libiconv and zlib that PyAV's
  wheel carries as DLLs;
- `SM64Trainer-third-party-source-unicorn.tar`: Unicorn's own source
  release, which its wheel is built from.

The compiler's own runtime is not in them: libgcc and libstdc++ (GCC),
winpthreads and the MinGW-w64 C runtime, which the linker puts into
`ffmpeg.exe` and PyAV's wheel carries as DLLs. Each is part of the
compiler's normal packaging and serves only to let the program run with
it, a System Library that GPL v3 section 1 leaves out of the Corresponding
Source. Their own licenses (the GCC Runtime Library Exception; MIT- and
BSD-style) ask for their notices, which are in `ffmpeg/` and
`pyav-ffmpeg/`. `third-party-sources.json` names each package under
`not_attached`, with the passages that decide it.

Each has a `.sha256` file beside it. `tools/corresponding_source.py` writes
them from `third-party-sources.json`, which pins every archive by URL and
SHA-256, and `tools/release.py` attaches them to every release. The build
scripts are in this repository at the release's tag.

| Component | In the app | License | Texts | Source |
| --- | --- | --- | --- | --- |
| FFmpeg 8.1.1, built for the trainer | `ffmpeg.exe`: records and converts replay video | GPL-3.0-or-later as built (`--enable-gpl --enable-version3`), plus the licenses of the libraries inside it: x264 (GPL-2.0-or-later), dav1d, libvpl, NVIDIA's and AMD's headers, zlib, the MinGW-w64 runtime and the GCC runtime | `ffmpeg/` (`BUILD-README.txt` names every part and the configure line) | The source archives on this release's page: `SM64Trainer-third-party-source-ffmpeg.tar`. `tools/build_ffmpeg.py` builds it from the inputs `ffmpeg/build-inputs.json` pins |
| FFmpeg inside PyAV | `av.libs/`: decodes and compresses replay video | GPL-3.0 as built, plus each library's license | `pyav-ffmpeg/` | The source archives on this release's page: `SM64Trainer-third-party-source-ffmpeg.tar` (PyAV's recipe is <https://github.com/PyAV-Org/pyav-ffmpeg/tree/8.1.1-2>; `pyav-ffmpeg/README.md` lists every library) |
| LINK's GLideN64 v4.2, modified | `sm64_trainer_gfx.dll`: the graphics plugin, the renderer with its replay capture in one file | GPL-2.0 (the trainer's additions: GPL-2.0-or-later) | `renderer/GPL-2.0.txt`, `renderer/GLideN64/` (with the licenses of the code it includes: GLSL-FXAA, Glow, gles2n64, GlideHQ; upstream's xBRZ, GPL-3.0 only, is left out of this build) | The source archives on this release's page: `SM64Trainer-third-party-source-plugin.tar`, with the overlay and the build steps in this repository (`renderer/README.md`, "Credit", lists them) |
| Qt 5.12.12 (qtbase), static | Inside the renderer: its settings window | LGPL-3.0, GPL-2.0 or GPL-3.0, as each file's own header offers; the plugin takes it under the GPL v2. Qt's own third-party parts are under the permissive licenses `THIRD-PARTY.txt` lists, and QtCore carries freedesktop.org's MIME database (GPL) | `renderer/Qt/` (`LICENSE.GPL2`, the LGPL and GPL v3 texts, and `THIRD-PARTY.txt`, every qtbase attribution; the settings window links only part of Qt) | The source archives on this release's page: `SM64Trainer-third-party-source-plugin.tar` (from <https://download.qt.io/archive/qt/5.12/5.12.12/submodules/>); `tools/build_renderer_libs.py` builds it and the settings window from it |
| FreeType 2.5.3 | Inside the renderer: on-screen text (Qt is built without FreeType) | FreeType License or GPL-2.0-or-later; the plugin takes it under the GPL v2 | `renderer/FreeType/` (`LICENSE.TXT` offers the choice) | The source archives on this release's page: `SM64Trainer-third-party-source-plugin.tar`; `tools/build_renderer_libs.py` builds it |
| libpng 1.6.32, zlib 1.2.8 and 1.2.11 | Inside the renderer (GlideHQ; Qt's own zlib copy is 1.2.11, and its PNG reader was compiled against libpng 1.6.37's header) | libpng, zlib | `renderer/libpng/`, `renderer/zlib/` | The source archives on this release's page: `SM64Trainer-third-party-source-plugin.tar`; `tools/build_renderer_libs.py` builds libpng 1.6.32 and zlib 1.2.8; zlib 1.2.11 and libpng's 1.6.37 header are in the Qt source |
| xxHash | Inside the renderer | BSD-2-Clause | `renderer/xxHash/` | The renderer's upstream tree, `src/xxHash/` |
| Unicorn 2.1.4 | The Python package `unicorn`, with its engine `unicorn.dll`: the Ghost Lab runs the game's own code in it (the free camera, rebuilt ghosts, save states) | The engine: GPL-2.0 only (its own code and the QEMU code it is built from; parts LGPL-2.1-or-later, BSD, MIT). The Python binding: BSD | `python/unicorn/` (`COPYING`, QEMU's note, the LGPL texts, `THIRD-PARTY-NOTICES.txt` with each permissive file's notice) | The source archives on this release's page: `SM64Trainer-third-party-source-unicorn.tar` (PyPI's `unicorn-2.1.4.tar.gz`, the file `uv.lock` pins) |
| cxd4 (mupen64plus-rsp-cxd4) | `scene_cxd4.dll` in `sm64_events/data/lab/`: the Ghost Lab runs the game's own audio microcode in it to play the music and the sound effects apart | CC0-1.0 | `cxd4/` | `tools/scene_lab/cxd4/` in this repository (its README names the pinned upstream commit) |
| NVIDIA Video Codec SDK header | `SM64GpuEncoderV1.dll`: hardware encoding on NVIDIA cards | MIT | `nvidia-video-codec-headers/` | `plugin/gfxwrap/vendor/nvEncodeAPI.h` in this repository |
| Preact 10.24.3 | The interface | MIT | `preact/` | <https://github.com/preactjs/preact/tree/10.24.3> |
| htm 3.1.1 | The interface's markup | Apache-2.0 | `htm/` | <https://github.com/developit/htm/tree/3.1.1> |
| Microsoft Edge WebView2 SDK 1.0.3856.49 | The desktop window (shipped inside pywebview) | Microsoft's BSD-style license, with its notices | `webview2/` | <https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49> |
| Google Sans | The Google sign-in button | OFL-1.1 | Beside the font: `ui/assets/community/google-sans-OFL.txt` (Google's own OFL.txt; its first line names Google Sans Flex, and the font file states its own copyright) | <https://github.com/googlefonts/googlesans> |
| Super Mario 256 | The rank caps' division digits | Listed "100% Free" by its author on dafont; the font carries no license text | `super-mario-256/` | <https://www.dafont.com/super-mario-256.font> |
| Python 3.13 | The interpreter and the DLLs it brings (OpenSSL, libffi, bzip2, SQLite ...), with Microsoft's C runtime DLLs beside it (`VCRUNTIME140.dll`, `MSVCP140.dll`, the Universal CRT) | PSF License and the licenses in its LICENSE.txt; the C runtime under Microsoft's redistribution terms (to the GPL it is a system library, the compiler's and Windows' own) | `python/CPython-<version>/`, added at build time | <https://www.python.org/downloads/source/> |
| Python packages | Everything PyInstaller bundles | Each package's own | `python/<package>-<version>/`, collected at build time from what the exe actually contains | Each package's source release on PyPI, at that version. Three folders are tracked here: `python/proxy-tools/` and `python/unicorn/`, because their wheels install no license file, and `python/pyaudiowpatch/`, the PyAudio and PortAudio licenses for the code its wheel compiles in but does not name |

The SM64 decompilation and libsm64 are CC0, so they are credited in About
and need no text here. That covers the address maps too. The app carries
STROOP's `MappingUS.map` and `MappingJP.map` whole, as STROOP's repository
holds them at commit `6771197ad0f50718d3d4a13387e01d1e6d9c0d1a`
(`STROOP/Mappings/`; SHA-256 `d942ba97…` and `5e518529…`, 879,744 and
812,217 bytes): the Ghost Lab reads their symbols and section rows to run
the game's own code. `behaviours_*.tsv` and `symbols_*.tsv` are (address,
symbol) pairs from the same two files. Both are GNU ld linker maps of a
decompilation build: they open with the linker's own "Archive member
included to satisfy reference by file (symbol)" and list its "Memory
Configuration", "Linker script" and the objects it linked
(`build/us/src/game/...`). STROOP publishes no license (its repository at
that commit has no license file), but the maps are output of the
decompilation build, not STROOP's own code; STROOP is credited as where we
got them.

Nintendo's course paintings are not distributed: the app copies each one out
of the player's own Usamune ROM when it runs (`src/sm64_events/course_art.py`).

When a release changes one of these (a new ffmpeg.exe, a PyAV bump, a new
vendored file), refresh its texts and its row. The build refuses an
ffmpeg.exe or PyAV the texts do not describe, and a bundled Python package
with no license file.

# Third-party notices

SM64 Trainer is free software under the GNU GPL v3 or later (`LICENSE` at
the repository root, and `SM64-Trainer/LICENSE` in an installed copy). Its
source is <https://github.com/griffinbeels/SM64-Trainer>; each release is a
tag there.

The trainer's own code that is built into the graphics plugin, or that loads
the plugin into its own process, is under the GNU GPL v2 or later instead
(`renderer/GPL-2.0.txt` is version 2). The plugin is linked with LINK's
GLideN64, which is GPL v2 only, and GPL v3 code cannot be combined with GPL
v2-only code; "v2 or later" combines with both. That code is:

- `plugin/gfxwrap/`, as its `LICENSE.md` says (not `vendor/` or `zilmar.h`);
- `renderer/model/`, the pose model linked into the plugin;
- the tools that build the plugin and write its edits into GLideN64's files:
  `tools/build_renderer.py`, `build_renderer_libs.py` (with the patches in
  `renderer/patches/`), `build_plugin.py`, `build_model.py`,
  `native_identity.py`, `native_build_cache.py`, `stage_link_witness.py`,
  `stage_ghost_renderer.py` and `stage_ghost_presentation.py`;
- the scene-lab hosts that load the plugin: `tools/scene_lab/viewer_host.c`,
  `replay_host.c` and `renderer_load.h`.

The plugin as one file follows GLideN64's license, GPL v2.

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
  wheel carries as DLLs.

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
| cxd4 (mupen64plus-rsp-cxd4) | Not in the installed app: the scene lab, from source, runs the game's own audio microcode in it to split music from sound effects | CC0-1.0 | `cxd4/` | `tools/scene_lab/cxd4/` in this repository (its README names the pinned upstream commit) |
| NVIDIA Video Codec SDK header | `SM64GpuEncoderV1.dll`: hardware encoding on NVIDIA cards | MIT | `nvidia-video-codec-headers/` | `plugin/gfxwrap/vendor/nvEncodeAPI.h` in this repository |
| Preact 10.24.3 | The interface | MIT | `preact/` | <https://github.com/preactjs/preact/tree/10.24.3> |
| htm 3.1.1 | The interface's markup | Apache-2.0 | `htm/` | <https://github.com/developit/htm/tree/3.1.1> |
| Microsoft Edge WebView2 SDK 1.0.3856.49 | The desktop window (shipped inside pywebview) | Microsoft's BSD-style license, with its notices | `webview2/` | <https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49> |
| Google Sans | The Google sign-in button | OFL-1.1 | Beside the font: `ui/assets/community/google-sans-OFL.txt` (Google's own OFL.txt; its first line names Google Sans Flex, and the font file states its own copyright) | <https://github.com/googlefonts/googlesans> |
| Super Mario 256 | The rank caps' division digits | Listed "100% Free" by its author on dafont; the font carries no license text | `super-mario-256/` | <https://www.dafont.com/super-mario-256.font> |
| Python 3.13 | The interpreter and the DLLs it brings (OpenSSL, libffi, bzip2, SQLite ...), with Microsoft's C runtime DLLs beside it (`VCRUNTIME140.dll`, `MSVCP140.dll`, the Universal CRT) | PSF License and the licenses in its LICENSE.txt; the C runtime under Microsoft's redistribution terms (to the GPL it is a system library, the compiler's and Windows' own) | `python/CPython-<version>/`, added at build time | <https://www.python.org/downloads/source/> |
| Python packages | Everything PyInstaller bundles | Each package's own | `python/<package>-<version>/`, collected at build time from what the exe actually contains | Each package's source release on PyPI, at that version. Two folders are tracked here: `python/proxy-tools/`, because its wheel installs no license file, and `python/pyaudiowpatch/`, the PyAudio and PortAudio licenses for the code its wheel compiles in but does not name |

The SM64 decompilation and libsm64 are CC0, so they are credited in About
and need no text here. That covers the address maps too: `behaviours_*.tsv`
and `symbols_*.tsv` are (address, symbol) pairs from STROOP's
`MappingUS.map` and `MappingJP.map`, which are GNU ld linker maps of a
decompilation build (their entries read `build/us/src/game/...`). STROOP
publishes no license, but those pairs come from the decompilation build, not
from STROOP's own code; STROOP is credited as where we got them.

When a release changes one of these (a new ffmpeg.exe, a PyAV bump, a new
vendored file), refresh its texts and its row. The build refuses an
ffmpeg.exe or PyAV the texts do not describe, and a bundled Python package
with no license file.

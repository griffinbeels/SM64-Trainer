# The renderer: LINK's GLideN64 v4.2 with the Practice Replay capture overlay

The graphics plugin the trainer installs into Project64 is two files. The
capture wrapper (`sm64_trainer_gfx.dll`, built from `plugin/gfxwrap/`) is
what Project64 selects; it loads this renderer (`GLideN64_SM64Trainer.dll`)
and takes finished frames from it through the SourceV2 export the overlay
adds. A stock GLideN64 has no such export, so the wrapper alone captures
nothing.

## Credit

The renderer is [LINK's GLideN64 v4.2](https://github.com/Luna-Project64/GLideN64),
the Luna-Project64 fork of [GLideN64](https://github.com/gonetz/GLideN64) by
Sergey Lipskiy, with Olivieryuyu, Ryan Rosser and the GLideN64 contributors,
which itself credits Orkin (glN64), yongzh (gles2n64), Hiroshi Morii (GlideHQ)
and ziggy (z64). These are the credits the pinned source names in its own
About dialog. griffman1212 built the capture overlay on top of it.

GLideN64 is licensed under the GNU GPL v2 (`LICENSE` in the upstream tree),
so this modified build is too. The complete corresponding source is:

- the upstream tree at commit `d0d1010`, pinned by archive and tree hash in
  `build-inputs.json`;
- the overlay in `plugin/gfxwrap/` (`link_dispatch.*`, `renderer_boundary.*`,
  `link_source_api.*`, `context_lifetime.*`, `renderer_gl_state.h`,
  `gl_snapshot.h`, `source_format.h`) and the edits
  `tools/stage_link_witness.py` makes to the upstream files;
- the project adaptations and build steps in `tools/build_renderer.py`.

Project64 shows the wrapper's About box (credits and the build id); the
renderer's own About dialog stays reachable by selecting the renderer DLL
directly in Project64.

## Building

    uv run python tools/build_renderer.py

The tool downloads the pinned upstream archive into the build cache
(`%LOCALAPPDATA%\SM64Trainer\build-cache\renderer`), verifies it, stages the
overlay in a scratch folder, builds Release/Win32 with MSBuild (x86 MSVC,
static CRT) and writes `src/sm64_events/data/plugin/GLideN64_SM64Trainer.dll`
and nothing else. The DLL carries a build id
(`SM64TrainerRendererIdentity`, a digest of the inputs above), which is how
the setup screen decides whether an installed renderer is this build's and
how `tests/test_renderer_build.py` checks the bundled file matches the tree.

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

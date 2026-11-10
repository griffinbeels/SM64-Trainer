# cxd4: the RSP interpreter the sound stems run the game's audio microcode in

These files are the interpreter core of mupen64plus-rsp-cxd4 at commit
`00906a92641c540a64e4e7505012157315314810`
(<https://github.com/mupen64plus/mupen64plus-rsp-cxd4/tree/00906a92641c540a64e4e7505012157315314810>),
copied unmodified. The project dedicates itself to the public domain under
CC0 1.0 (`COPYING`, also `licenses/cxd4/COPYING`).

`tools/scene_lab/stems_lle.c` stands in for the plugin module (`module.c`,
not copied: it reads a config file and reports through a console window). It
compiles with `su.c` and `vu/*.c` into the 64-bit DLL that
`tools/scene_lab/stems_probe.py` (`build_lle`) and the stem renderer
(`tools/scene_lab/stems_live.py`) load, built once per input hash with the
x64 MSVC toolchain into the scene lab's build cache.

Kept: `su.c su.h module.h rsp.h my_types.h vu/` and the license. Left out:
the plugin module, the mupen64plus API headers, the ARM `sse2neon` shim and
the build scripts, none of which the x64 build includes.

Vendored rather than fetched at build time (2026-10-27): the source is small
(260 KB), the license asks nothing, and a fetch would make the lab's sound
depend on the network and on the upstream history staying put. To move to a
newer revision, copy the same files from it, update the commit above and in
`stems_probe.py` (`LLE_REVISION`), and rerun the stems checks (G1 must stay
bit for bit).

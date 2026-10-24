"""SourceV3 sidecar ownership without changing the SourceV2 record ABI, and the
SourceV4 display descriptor the ghost copy records from."""
import importlib.util
import subprocess
from pathlib import Path

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"


def _build():
    spec = importlib.util.spec_from_file_location("descriptor3_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    assert vc, "SourceV3 witness requires x86 MSVC"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    return build, vc, flags


def test_descriptor_ticket_scope(tmp_path):
    build, vc, flags = _build()
    executable = tmp_path / "source_descriptor3_host.exe"
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}",
        str(SOURCE / "renderer_boundary.cpp"), str(ROOT / "tests/native/source_descriptor3_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=20, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "wrong-thread=refused ABA=refused cancellation=refused" in result.stdout


# The ghost renderer's link_source_api.cpp needs its renderer's headers; these
# stand in for them so the real query_display runs over the real boundary.
FIXTURE_HEADERS = {
    "GLFunctions.h": b"""#pragma once
#include <windows.h>
#include <stdint.h>
#include "source_refusal.h"
namespace renderer { struct Identity { uintptr_t context,read_drawable; uint32_t owner_thread,context_generation,drawable_generation; }; }
namespace snapshot { struct RestoreBindings { unsigned read_framebuffer,texture_2d,source_read_buffer; }; }
namespace replay_gl { inline bool CandidateBindings(renderer::Identity *i,snapshot::RestoreBindings *b) {
*i={123,456,GetCurrentThreadId(),1,1};*b={17,19,0x405};return true;}
inline bool DrawableExtent(unsigned *w,unsigned *h){*w=640;*h=480;return true;}
inline uint32_t SourceFormat(){return 1;}
inline void BindingsCause(rs_bindings_cause *c){*c={};}
inline bool __cdecl BeginCapture(){return true;} inline bool __cdecl EndCapture(){return true;} }
""",
    "DisplayWindow.h": b"""#pragma once
extern unsigned swaps;
struct Display { unsigned getWidth(){return 320;} unsigned getHeight(){return 240;}
unsigned getHeightOffset(){return 0;} unsigned getBuffersSwapCount(){return swaps;} };
inline Display &dwnd(){static Display d;return d;}
""",
    "N64.h": b"#pragma once\nstruct Registers {unsigned *VI_ORIGIN;}; extern Registers REG;\n",
    "PluginAPI.h": b"""#pragma once
#include "renderer_boundary.h"
struct PluginAPI { void UpdateScreenCaptured(rb_ticket t); };
inline PluginAPI &api(){static PluginAPI p;return p;}
""",
    "GraphicsDrawer.h": b"#pragma once\nstruct GraphicsDrawer { struct CopyRectParams{}; struct BlitOrCopyRectParams{}; };\n",
}


def test_display_descriptor_follows_the_clean_one(tmp_path):
    """I1: DISPLAY_VI carries the clean descriptor's geometry and identity with
    the display surface's framebuffer and generation; SAME_AS_CLEAN sets only
    bytes, version, kind and update_occurrence; UNAVAILABLE while the consumer
    is off; any ticket the V3 query refuses returns 0."""
    build, vc, flags = _build()
    for name, text in FIXTURE_HEADERS.items():
        (tmp_path / name).write_bytes(text)
    executable = tmp_path / "source_display_host.exe"
    build._cl(vc, flags + ["/std:c++17", "/EHsc", "/DSM64_GHOST_RENDERER", f"/I{tmp_path}", f"/I{SOURCE}",
        str(SOURCE / "link_source_api.cpp"), str(SOURCE / "renderer_boundary.cpp"),
        str(ROOT / "tests/native/source_display_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", *build.LIBS], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=20, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "display=clean-geometry same=four-fields off=unavailable foreign=refused stale=refused" in result.stdout


def test_display_kinds_extend_the_v3_kinds():
    text = (SOURCE / "source_descriptor3.h").read_text(encoding="utf-8")
    for kind, value in [("RS_SOURCE_UNAVAILABLE", 0), ("RS_SOURCE_DEFAULT_FRONT", 1), ("RS_SOURCE_CLEAN_VI", 2),
                        ("RS_SOURCE_NO_NEW_PRESENTATION", 3), ("RS_SOURCE_DISPLAY_VI", 4),
                        ("RS_SOURCE_SAME_AS_CLEAN", 5)]:
        assert f"{kind}={value}" in text, kind

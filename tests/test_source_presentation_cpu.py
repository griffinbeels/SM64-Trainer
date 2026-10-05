"""Public source classification with CPU fixtures, without WGL or GPU work."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


@pytest.mark.parametrize("restore_private_alias", [False, True])
def test_qualified_front_source_and_refusals(tmp_path, restore_private_alias):
    spec = importlib.util.spec_from_file_location("source_presentation_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "CPU source witness requires x86 MSVC"
    (tmp_path / "GLFunctions.h").write_text("""#pragma once
#include <windows.h>
#include <stdint.h>
namespace renderer { struct Identity { uintptr_t context,read_drawable; uint32_t owner_thread,context_generation,drawable_generation; }; }
namespace snapshot { struct RestoreBindings { unsigned read_framebuffer,texture_2d,source_read_buffer; }; }
namespace replay_gl { inline bool CandidateBindings(renderer::Identity *i,snapshot::RestoreBindings *b) {
*i={123,456,GetCurrentThreadId(),1,1};*b={17,19,0x405};return true;}
inline bool DrawableExtent(unsigned *w,unsigned *h){*w=640;*h=480;return true;}
inline uint32_t SourceFormat(){return 2;}
inline bool __cdecl BeginCapture(){return true;} inline bool __cdecl EndCapture(){return true;} }
""", encoding="utf-8")
    (tmp_path / "GraphicsDrawer.h").write_text("""#pragma once
struct GraphicsDrawer { struct CopyRectParams {}; struct BlitOrCopyRectParams {}; };
""", encoding="utf-8")
    (tmp_path / "DisplayWindow.h").write_text("""#pragma once
extern unsigned source_swaps;
struct Display { unsigned getWidth(){return 320;} unsigned getHeight(){return 240;}
unsigned getHeightOffset(){return 0;} unsigned getBuffersSwapCount(){return source_swaps;} };
inline Display &dwnd(){static Display d;return d;}
""", encoding="utf-8")
    (tmp_path / "N64.h").write_text("#pragma once\nstruct Registers {unsigned *VI_ORIGIN;}; extern Registers REG;\n", encoding="utf-8")
    (tmp_path / "PluginAPI.h").write_text("""#pragma once
#include "renderer_boundary.h"
extern unsigned source_swaps; extern bool source_swap_success;
extern "C" void rs_before_update();
struct PluginAPI { void UpdateScreenCaptured(rb_ticket t) {
auto owned=rb_source_begin(t);rs_before_update();if(source_swap_success)++source_swaps;rb_source_end(owned);} };
inline PluginAPI &api(){static PluginAPI p;return p;}
""", encoding="utf-8")
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    target = tmp_path / "source_presentation_cpu.exe"
    source = NATIVE / "link_source_api.cpp"
    if restore_private_alias:
        text = source.read_text(encoding="utf-8")
        anchor = "    if(clean.kind!=RS_SOURCE_DEFAULT_FRONT)return 1;\n"
        assert text.count(anchor) == 1
        source = tmp_path / "link_source_api.cpp"
        source.write_text(text.replace(anchor, ""), encoding="utf-8")
    build._cl(vcvars, flags + ["/std:c++17", "/EHsc", "/DSM64_GHOST_RENDERER", f"/I{tmp_path}", f"/I{NATIVE}",
        str(source), str(NATIVE / "renderer_boundary.cpp"),
        str(ROOT / "tests/native/source_presentation_cpu_host.cpp"), f"/Fe:{target}", f"/Fo{tmp_path}\\",
        "/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", "kernel32.lib"], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True, timeout=10,
                            cwd=tmp_path, check=False, **quiet_spawn_kwargs())
    if restore_private_alias:
        assert result.returncode != 0
        assert "private clean does not prove displayed equality" in result.stderr
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert "front=truthful clean=private stale=refused revoke=refused failed_swap=omitted twin=exact_or_unavailable front_alias=same_occurrence" in result.stdout

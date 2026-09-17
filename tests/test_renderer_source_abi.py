"""Load the real source API as a DLL, with independent callback-module lifetime."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


@pytest.fixture(scope="module")
def abi_host(tmp_path_factory):
    work = tmp_path_factory.mktemp("source_abi")
    spec = importlib.util.spec_from_file_location("source_abi_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "native source ABI requires x86 MSVC"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{work}", f"/I{NATIVE}"]
    (work / "GLFunctions.h").write_bytes(b"""#pragma once
#include <windows.h>
#include <stdint.h>
namespace renderer { struct Identity { uintptr_t context,read_drawable; uint32_t owner_thread,context_generation,drawable_generation; }; }
namespace snapshot { struct RestoreBindings { unsigned read_framebuffer,texture_2d,source_read_buffer; }; }
namespace replay_gl { inline bool CandidateBindings(renderer::Identity *i,snapshot::RestoreBindings *b) {
*i={123,456,GetCurrentThreadId(),1,1};*b={17,19,0x405};return true;}
inline bool DrawableExtent(unsigned *w,unsigned *h){*w=640;*h=480;return true;}
inline uint32_t SourceFormat(){return 1;}
inline bool __cdecl BeginCapture(){return true;} inline bool __cdecl EndCapture(){return true;} }
""")
    (work / "DisplayWindow.h").write_bytes(b"""#pragma once
struct Display { unsigned getWidth(){return 320;} unsigned getHeight(){return 240;}
unsigned getHeightOffset(){return 0;} unsigned getBuffersSwapCount(){return 1;} };
inline Display &dwnd(){static Display d;return d;}
""")
    (work / "N64.h").write_bytes(b"#pragma once\nstruct Registers {unsigned *VI_ORIGIN;}; extern Registers REG;\n")
    (work / "PluginAPI.h").write_bytes(b"""#pragma once
#include "renderer_boundary.h"
struct PluginAPI { void UpdateScreenCaptured(rb_ticket t) {auto owned=rb_source_begin(t);rb_source_end(owned);} };
inline PluginAPI &api(){static PluginAPI p;return p;}
""")
    (work / "platform.cpp").write_bytes(b"""#include "N64.h"
#include "renderer_boundary.h"
unsigned origin=123; Registers REG{&origin};
extern "C" void __cdecl SourceTestLifecycle(int mode) {if(mode==1)rb_rom_open();else if(mode==0)rb_rom_closed();else rb_close();}
#pragma comment(linker,"/EXPORT:SourceTestLifecycle=_SourceTestLifecycle")
""")
    (work / "callbacks.cpp").write_bytes(b"""#include "renderer_boundary.h"
static unsigned captures=0,completions=0;
extern "C" int __cdecl Capture(const rb_record *r,rb_image *i) {
 if(r->stamp.bytes[0]!=77)return 0; ++captures; *i={r->occurrence,r->slot,r->epoch,RB_IMAGE_SUBMITTED,0};return 1;}
extern "C" int __cdecl Completion(const rb_image *i){++completions;return i->serial!=0;}
extern "C" unsigned __cdecl Captured(){return captures;}
extern "C" unsigned __cdecl Completed(){return completions;}
#pragma comment(linker,"/EXPORT:Capture=_Capture")
#pragma comment(linker,"/EXPORT:Completion=_Completion")
#pragma comment(linker,"/EXPORT:Captured=_Captured")
#pragma comment(linker,"/EXPORT:Completed=_Completed")
""")
    link = ["/link", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *build.LIBS]
    build._cl(vcvars, flags + ["/LD", str(NATIVE / "link_source_api.cpp"),
        str(NATIVE / "renderer_boundary.cpp"), str(work / "platform.cpp"),
        f"/Fe:{work / 'source_api.dll'}", f"/Fo{work}\\", *link], work)
    build._cl(vcvars, flags + ["/LD", str(work / "callbacks.cpp"),
        f"/Fe:{work / 'source_callbacks.dll'}", f"/Fo{work}\\", *link], work)
    target = work / "source_abi.exe"
    build._cl(vcvars, flags + ["/Gz", str(NATIVE / "renderer_source_abi_host.cpp"),
        f"/Fe:{target}", f"/Fo{work}\\", *link], work)
    return target


@pytest.mark.parametrize("mode", ["normal", "late", "closed"])
def test_public_source_registration(abi_host, mode):
    result = subprocess.run([str(abi_host), mode], capture_output=True, text=True, timeout=10,
                            cwd=abi_host.parent, **quiet_spawn_kwargs(), check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "source ABI passed:" in result.stdout

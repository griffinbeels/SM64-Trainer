"""Drive adapter through the real SourceV2 DLL and a threaded renderer fixture."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    work = tmp_path_factory.mktemp("stamp_adapter")
    spec = importlib.util.spec_from_file_location(
        "stamp_build", ROOT / "tools/build_plugin.py"
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars
    flags = [f for f in build.COMMON_FLAGS if not f.startswith("/std:")]
    flags += ["/std:c++17", "/EHsc", f"/I{work}", f"/I{NATIVE}"]
    (work / "GLFunctions.h").write_text(r"""#pragma once
#include <windows.h>
#include <stdint.h>
extern bool surface_ready;
namespace renderer { struct Identity { uintptr_t context,read_drawable; uint32_t owner_thread,context_generation,drawable_generation; }; }
namespace snapshot { struct RestoreBindings { unsigned read_framebuffer,texture_2d,source_read_buffer; }; }
namespace replay_gl {
inline bool CandidateBindings(renderer::Identity *i,snapshot::RestoreBindings *b){if(!surface_ready)return false;*i={123,456,GetCurrentThreadId(),1,1};*b={0,0,0x404};return true;}
inline bool DrawableExtent(unsigned*w,unsigned*h){*w=640;*h=480;return true;}
inline uint32_t SourceFormat(){return 1;}
inline bool __cdecl BeginCapture(){return true;}inline bool __cdecl EndCapture(){return true;}
}
""")
    (work / "DisplayWindow.h").write_text(r"""#pragma once
extern unsigned swap_count;
struct Display {unsigned getWidth(){return 320;}unsigned getHeight(){return 240;}
unsigned getHeightOffset(){return 0;}unsigned getBuffersSwapCount(){return swap_count;}};
inline Display&dwnd(){static Display d;return d;}
""")
    (work / "N64.h").write_text(
        "#pragma once\nstruct Registers{unsigned *VI_ORIGIN;};extern Registers REG;\n"
    )
    (work / "PluginAPI.h").write_text(r"""#pragma once
#include "renderer_boundary.h"
struct PluginAPI{void UpdateScreenCaptured(rb_ticket);};
inline PluginAPI&api(){static PluginAPI p;return p;}
""")
    (work / "platform.cpp").write_text(r"""#include "PluginAPI.h"
#include "N64.h"
#include <thread>
#include <mutex>
#include <condition_variable>
unsigned actual_origin=0,swap_count=0,delta=1,swaps=1;bool surface_ready=true;
Registers REG{&actual_origin};HANDLE entered=nullptr,resume=nullptr;
std::mutex mutex;std::condition_variable changed;bool queued=false,done=false,quit=false;rb_ticket ticket{};std::thread worker;
void PluginAPI::UpdateScreenCaptured(rb_ticket t){std::unique_lock<std::mutex>lock(mutex);ticket=t;queued=true;done=false;changed.notify_all();changed.wait(lock,[]{return done;});queued=false;}
extern "C" void __cdecl TestRom(int mode){
 if(mode==1){rb_rom_open();worker=std::thread([]{std::unique_lock<std::mutex>lock(mutex);for(;;){changed.wait(lock,[]{return (queued&&!done)||quit;});if(quit)break;auto next=ticket;lock.unlock();
 auto owned=rb_source_begin(next);if(entered){SetEvent(entered);if(WaitForSingleObject(resume,5000)!=WAIT_OBJECT_0)ExitProcess(2);}
 actual_origin+=delta;swap_count+=swaps;rb_source_end(owned);lock.lock();done=true;changed.notify_all();}});}
 else if(mode==0){rb_rom_closed();{std::lock_guard<std::mutex>lock(mutex);quit=true;changed.notify_all();}worker.join();}else rb_close();}
extern "C" void __cdecl TestUpdate(){api().UpdateScreenCaptured({});}
extern "C" unsigned __cdecl TestOrigin(){return actual_origin;}
extern "C" void __cdecl TestScenario(unsigned d,unsigned s,int ready){delta=d;swaps=s;surface_ready=ready!=0;}
extern "C" void __cdecl TestPause(HANDLE a,HANDLE b){entered=a;resume=b;}
#pragma comment(linker,"/EXPORT:TestRom=_TestRom")
#pragma comment(linker,"/EXPORT:TestUpdate=_TestUpdate")
#pragma comment(linker,"/EXPORT:TestOrigin=_TestOrigin")
#pragma comment(linker,"/EXPORT:TestScenario=_TestScenario")
#pragma comment(linker,"/EXPORT:TestPause=_TestPause")
""")
    link = [
        "/link",
        "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'",
        "bcrypt.lib",
        *build.LIBS,
    ]
    module = work / "stamp_source.dll"
    build._cl(
        vcvars,
        flags
        + [
            "/LD",
            str(NATIVE / "link_source_api.cpp"),
            str(NATIVE / "renderer_boundary.cpp"),
            str(work / "platform.cpp"),
            f"/Fe:{module}",
            f"/Fo{work}\\",
            *link,
        ],
        work,
    )
    target = work / "stamp_adapter.exe"
    build._cl(
        vcvars,
        flags
        + [
            "/Gz",
            "/DSA_TEST_HOST",
            str(NATIVE / "stamp_adapter.cpp"),
            str(NATIVE / "stamp_adapter_host.cpp"),
            f"/Fe:{target}",
            f"/Fo{work}\\",
            *link,
        ],
        work,
    )
    return target, module


@pytest.mark.parametrize(
    "mode",
    [
        "quiescence",
        "normal",
        "omissions",
        "epochs",
        "invalid",
        "custody",
        "capacity",
        "refusal",
        "cancellation",
        "frontier_before",
        "frontier_after",
        "frontier_finish",
        "frontier_source",
        "late",
    ],
)
def test_adapter(host, mode):
    target, module = host
    result = subprocess.run(
        [str(target), str(module), mode],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=target.parent,
        **quiet_spawn_kwargs(),
        check=False,
    )
    (target.parent / f"{mode}.txt").write_text(
        result.stdout + result.stderr, encoding="utf-8"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"stamp adapter passed: {mode}" in result.stdout

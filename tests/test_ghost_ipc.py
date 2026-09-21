"""Real hidden IPC worker/page and asset reader with isolated renderer interfaces."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_native_ghost_ipc_lifecycle(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 IPC worker")
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for this native witness"
    inputs = ["plugin/gfxwrap/ghost_ipc.cpp", "plugin/gfxwrap/ghost_ipc.h",
              "plugin/gfxwrap/ghost_assets.cpp", "plugin/gfxwrap/ghost_assets.h",
              "plugin/gfxwrap/ghost_clip.h", "plugin/gfxwrap/ghost_playback.h",
              "plugin/gfxwrap/ghost_origin.h", "plugin/gfxwrap/ghost_origin.cpp",
              "tests/native/ghost_ipc_host.cpp", "tests/test_ghost_ipc.py"]
    (tmp_path / "ipc-inputs.json").write_text(json.dumps({path: hashlib.sha256(
        (ROOT / path).read_bytes()).hexdigest() for path in inputs}, indent=2), encoding="utf-8")
    library = tmp_path / "ghost_ipc_witness.dll"
    target = tmp_path / "ghost_ipc_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy", "/LD",
        f"/I{ROOT / 'renderer/model'}", f"/I{ROOT / 'plugin/gfxwrap'}",
        str(ROOT / "plugin/gfxwrap/ghost_ipc.cpp"), str(ROOT / "plugin/gfxwrap/ghost_assets.cpp"),
        str(ROOT / "plugin/gfxwrap/ghost_origin.cpp"),
        str(ROOT / "tests/native/ghost_ipc_host.cpp"), f"/Fe:{library}", f"/Fo{tmp_path}\\",
        "/link", "/OPT:REF", "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'",
        *compiler.LIBS], tmp_path)
    # Real worker self-pinning/release must run in a DLL, as in the product.
    launcher = tmp_path / "launcher.cpp"
    launcher.write_text(r'''
#include <windows.h>
#include <cstdio>
int wmain(int argc,wchar_t** argv) {
    if(argc!=5)return 90;
    HMODULE module=LoadLibraryW(argv[1]);if(!module)return 91;
    auto run=reinterpret_cast<int (__cdecl *)(int,wchar_t**)>(GetProcAddress(module,"GhostIpcWitness"));
    if(!run){FreeLibrary(module);return 92;}
    const int result=run(argc-1,argv+1);FreeLibrary(module);
    if(result)return result;
    const DWORD start=GetTickCount();
    while(GetModuleHandleW(argv[1]) && GetTickCount()-start<2000)Sleep(5);
    if(GetModuleHandleW(argv[1])){std::fputs("IPC DLL remained pinned after retirement\n",stderr);return 93;}
    return 0;
}
''', encoding="utf-8")
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        str(launcher), f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *compiler.LIBS], tmp_path)
    header = struct.pack("<8s8I16sQqdQQIIii4I3Q2I", b"SM64GHO2", 2, 160, 260, 1,
                         1, 3, 1, 1, bytes([1]) + bytes(15), 10_000_000, 0, 1.0,
                         1, 1, 100, 100, 9, 1, 1, 100, 10, 0, 1, 0, 1, 0, 0)
    frame = bytearray(260)
    struct.pack_into("<8IQQqIHHhhII", frame, 0, 260, 2, 1, 3, 244, 3, 1, 3,
                     1, 1, 0, 100, 0, 10, 9, 1, 8, 0)
    struct.pack_into("<3f", frame, 88, 1.0, 1.0, 1.0)  # gc_pose.scale
    struct.pack_into("<h", frame, 106, 197)  # idle animation
    struct.pack_into("<2I", frame, 116, 0x0c400201, 0x0c400201)  # action/body_action
    struct.pack_into("<I", frame, 128, 1)  # GC_ROOT_TRS
    struct.pack_into("<4I6Q", frame, 196, 5, 1, 0, 10, 0, 0, 0, 1, 0, 1)
    clip = tmp_path / "fixture.ghost"
    clip.write_bytes(header + frame)
    rom = bytearray(8 << 20)
    rom[:4] = bytes.fromhex("80371240")
    rom[0x20:0x34] = b"SM64 USAMUNE v1.93u".ljust(20, b" ")
    rom[0x3e] = ord("E")
    rom_path = tmp_path / "fixture.z64"
    rom_path.write_bytes(rom)
    result = subprocess.run([str(target), str(library), str(clip), hashlib.sha256(clip.read_bytes()).hexdigest(),
                             str(rom_path)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=35, check=False, **quiet_spawn_kwargs())
    (tmp_path / "ipc-witness.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] and report["checks"] > 60
    assert report["commands"] >= 8 and report["bindings"] == report["releases"] >= 2

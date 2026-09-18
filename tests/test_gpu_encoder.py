"""CPU-injected NVENC failure cleanup and plain-C packet ABI."""
import importlib.util
from pathlib import Path
import subprocess

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
#: Every fault the host walks (`gpu_encoder_fault_host.cpp`'s main()). Each one
#: is a driver failure the encoder must survive without leaking a mapped input,
#: a locked bitstream or a half-closed session.
CASES = frozenset({
    "ok", "map", "map-partial", "encode", "need-more", "lock", "unlock",
    "unmap", "pts", "duration", "empty", "oversize", "null", "reject", "throw",
    "eos", "invalid-then-eos", "wrong-owner", "destroy-output", "unregister",
    "destroy-encoder",
})


def test_encoder_packet_cleanup(tmp_path):
    spec = importlib.util.spec_from_file_location("encoder_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc32 = build.find_vcvars32()
    assert vc32 and vc32.with_name("vcvars64.bat").is_file()
    vc64 = vc32.with_name("vcvars64.bat")
    native = ROOT / "plugin/gfxwrap"
    flags = [f for f in build.COMMON_FLAGS if not f.startswith("/std:")]
    exe = tmp_path / "encoder_fault.exe"
    build._cl(vc64, flags + ["/std:c++17", "/EHsc", "/DREPLAY_ENCODER_TESTING",
        f"/I{native}", f"/I{native / 'vendor'}", str(native / "gpu_bridge_encoder.cpp"),
        str(native / "gpu_encoder_fault_host.cpp"), f"/Fe:{exe}", f"/Fo{tmp_path}\\",
        "/link", "d3d11.lib", "dxgi.lib"], tmp_path)
    build._cl(vc64, flags + ["/TC", "/c", f"/I{native}",
        str(native / "gpu_encoder_c_host.c"), f"/Fo{tmp_path}\\"], tmp_path)
    result = subprocess.run([str(exe)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=10, **quiet_spawn_kwargs(), check=False)
    (tmp_path / "faults.log").write_bytes((result.stdout + result.stderr).encode())
    assert result.returncode == 0, result.stdout + result.stderr
    # The host CHECKs each case itself; what a count cannot see is a case
    # QUIETLY DROPPED from main()'s list. Name them, and allow new ones: a
    # missing fault case is a hole, an added one is coverage.
    ran = {line.split(maxsplit=2)[1] for line in result.stdout.splitlines()
           if line.startswith("PASS ")}
    assert CASES <= ran, f"fault cases no longer run: {sorted(CASES - ran)}\n{result.stdout}"

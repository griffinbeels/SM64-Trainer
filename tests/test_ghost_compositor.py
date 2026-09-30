"""Real hidden WGL compositor witness, independent of libsm64 and the emulator."""
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"
pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GHOST_COMPOSITOR") != "1",
    reason="explicit desktop OpenGL 4.3 witness: set SM64_TEST_GHOST_COMPOSITOR=1")


def run_compositor(tmp_path, source=SOURCE / "ghost_compositor.cpp"):
    spec = importlib.util.spec_from_file_location("compositor_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    if vc is None:
        pytest.skip("x86 MSVC unavailable")
    executable = tmp_path / "ghost_compositor_host.exe"
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{SOURCE}",
        str(source), str(ROOT / "tests/native/ghost_compositor_host.cpp"),
        f"/Fe:{executable}", f"/Fo{tmp_path}\\", "/link", "opengl32.lib", "gdi32.lib", "user32.lib"], tmp_path)
    result = subprocess.run([str(executable)], capture_output=True, text=True, cwd=tmp_path,
        timeout=40, check=False, **quiet_spawn_kwargs())
    return result


def test_real_compositor(tmp_path):
    result = run_compositor(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())
    assert "compositor passed:" in result.stdout
    assert "shadow=decal coplanar=once clean=unchanged hud=exact state=restored resize=yes contexts=2" in result.stdout
    # A frame with the renderer's whole record asks the driver nothing.
    assert "known=nothing_asked" in result.stdout
    assert "vanish=noise_body_only" in result.stdout


@pytest.mark.parametrize("before,after,failure", [
    ("if(noiseAlpha!=0 && material.a<bodyNoise()) discard;",
     "if(noiseAlpha<0 && material.a<bodyNoise()) discard;",
     "vanish has holes and surviving body pixels in both passes"),
    ("marioBody=gl_VertexID<bodyEnd ? 1 : 0;", "marioBody=bodyEnd>=0 ? 1 : 0;",
     "cap leaves held object shadow and particles unchanged"),
    ("g.ColorMaski(0,0,0,0,0);g.DrawArrays(TRIANGLES,first,pose.count);",
     "g.ColorMaski(0,0,0,0,0);g.DrawArrays(TRIANGLES,first,pose.count);\n"
     "        g.Uniform1i(p->uniforms[14],seed+1);",
     "vanish has holes and surviving body pixels in both passes"),
], ids=["no-noise-discard", "held-object-noise", "different-pass-seeds"])
def test_vanish_witness_refuses_broken_rendering(tmp_path, before, after, failure):
    """Calibrate the interior-hole, neighbor and two-pass probes independently.

    Keep the uniforms live in each mutant so a shader setup failure cannot
    masquerade as the rendered failure this test needs to observe.
    """
    original = (SOURCE / "ghost_compositor.cpp").read_text(encoding="utf-8")
    assert original.count(before) == 1
    source = tmp_path / "ghost_compositor.cpp"
    source.write_text(original.replace(before, after), encoding="utf-8")
    result = run_compositor(tmp_path, source)
    assert result.returncode != 0, "rendered witness accepted the deliberately broken cap effect"
    assert failure in result.stderr, result.stdout + result.stderr

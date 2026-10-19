"""A pre-channel renderer failure reaches the existing control backend."""
import subprocess

from sm64_events.core.childproc import quiet_spawn_kwargs

import gpu_render_witness as witness


def test_gl_prerequisite_receipt_without_picture_channel(tmp_path):
    build, vc32 = witness.toolchain()
    target = tmp_path / "prerequisites.exe"
    flags = witness._flags(build, ())
    build._cl(vc32, flags + [
        str(witness.SOURCE / "gpu_prerequisite_host.cpp"),
        str(witness.SOURCE / "runtime_delivery.cpp"),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", *build.LIBS,
    ], tmp_path)
    result = subprocess.run([str(target)], capture_output=True, text=True,
                            timeout=5, check=True, **quiet_spawn_kwargs())
    # These numbers are the ControlV1 contract, independently consumed by
    # Python. Detail zero/unknown keep the legacy reason for older/newer DLLs.
    assert result.stdout.splitlines() == [
        "prerequisite 0 10005", "prerequisite 1 10028",
        "prerequisite 2 10029", "prerequisite 3 10030",
        "prerequisite 4 10031", "prerequisite 5 10032",
        "prerequisite 6 10033", "prerequisite 7 10005", "other 10006",
    ]

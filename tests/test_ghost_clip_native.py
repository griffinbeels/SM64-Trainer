"""Compare Python decoding with actual gp.prepare_clip, without model/ROM/GL."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from ghost_native_fixture import frame
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.clip import ClipError, SEGMENT_CLOCK, decode_clip
from test_ghost_clip import BAD_CLOCK_WORDS, BAD_HEADER_WORDS, encoded, matrix_frame

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = Path(os.environ.get("SM64_GHOST_PLAYBACK_ROOT", ROOT)).resolve()


def corpus():
    base = encoded((frame(),))
    values = [base, encoded((matrix_frame(),)), encoded((frame(),), SEGMENT_CLOCK),
              encoded((frame(1, valid=29), frame(2))),
              encoded((frame(1, igt=99, elapsed=100), frame(4, igt=1, elapsed=104, area=2))),
              encoded((frame(1, origin=0), frame(2, origin=1))),
              encoded((frame(1), frame(2, origin=2), frame(3, origin=2))),
              encoded((frame(clock_valid=7, banked=30),)),
              encoded((frame(1, elapsed=0), frame(2, elapsed=2**64-1)), SEGMENT_CLOCK),
              base[:100], base[:-1], base + b"\0"]
    for offset, value in ((128, 3), (168, 1), (216, 0), (156, 0x7fc00000)):
        changed = bytearray(encoded((matrix_frame(),)))
        struct.pack_into("<I", changed, 160 + offset, value)
        values.append(bytes(changed))
    for status in range(12):
        values.append(encoded((frame(world=status),)))
        # Timing and origin are unchanged; absent pose remains a refusal even
        # when the historical renderer was ready or its target was unsupported.
        values.append(base[:160] + frame(world=status, valid=29))
    for offset, value in [*BAD_HEADER_WORDS, *((160 + at, value) for at, value in BAD_CLOCK_WORDS),
                           (160, 192), (164, 1), (180, 2), (188, 63),
                           (172, 12),
                           (160 + 72, 0), (160 + 76, 0x7fc00000), (160 + 248, 0x7f800000)]:
        changed = bytearray(base)
        struct.pack_into("<I", changed, offset, value)
        values.append(bytes(changed))
    for offset, value in [(32, 1), (40, 101), (48, 999), (56, 99), (308, 0), (316, 10)]:
        changed = bytearray(encoded((frame(1), frame(2))))
        struct.pack_into("<Q" if offset in (32, 40, 48, 308, 316) else "<I", changed, 160 + 652 + offset, value)
        values.append(bytes(changed))
    # Pose refusal reasons: kept on refused rows and on carried roots; an
    # unknown reason or any other pose byte on a refusal is refused.
    for reason in (1, 3, 11, 19, 20, 23):
        values.append(encoded((frame(1, valid=29, pose_refusal=reason), frame(2))))
    values.append(encoded((frame(1, pose_refusal=19),)))
    for offset, value in ((160 + 153, 24), (160 + 76, 1)):
        changed = bytearray(encoded((frame(1, valid=29, pose_refusal=19), frame(2))))
        changed[offset] = value
        values.append(bytes(changed))
    # Recorded shadow and sound: accepted shapes, then each native refusal.
    evidence = encoded((frame(1, shadow=True, sounds=(0x24228080,)),
                        frame(2, sounds=tuple(range(1, 9)), overflow=True)))
    values.append(evidence)
    for offset, pack, value in [(160 + 128, "<I", 1), (160 + 324 + 60, "<f", 0.5),
                                (160 + 324, "<I", 0x7fc00000), (160 + 324 + 163, "<B", 1),
                                (160 + 616, "<B", 9), (160 + 652 + 617, "<B", 2),
                                (160 + 616 + 1, "<B", 1), (160 + 620 + 4, "<I", 5),
                                (160 + 652 + 324 + 8, "<I", 1), (160 + 652 + 620, "<I", 0)]:
        changed = bytearray(evidence)
        struct.pack_into(pack, changed, offset, value)
        values.append(bytes(changed))
    return values


def test_python_and_native_acceptance_agree(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Win32 native playback component")
    spec = importlib.util.spec_from_file_location("clip_build", COMPONENTS / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    assert vcvars, "native codec parity needs the x86 compiler"
    native = COMPONENTS / "plugin/gfxwrap"
    host = ROOT / "tests/native/ghost_clip_host.cpp"
    source = native / "ghost_playback.cpp"
    target = tmp_path / "ghost_clip_host.exe"
    inputs = [source, native / "ghost_origin.cpp", host, *(native / name for name in (
        "ghost_clip.h", "ghost_capture.h", "ghost_playback.h", "ghost_origin.h", "ghost_clock.h", "ghost_pose.h")),
        COMPONENTS / "renderer/model/model.h", COMPONENTS / "tools/build_plugin.py",
        *(ROOT / name for name in ("src/sm64_events/ghost/clip.py", "tests/ghost_native_fixture.py",
                                   "tests/test_ghost_clip.py", "tests/test_ghost_clip_native.py"))]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    build._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{native}", f"/I{COMPONENTS / 'renderer/model'}", str(source), str(native / "ghost_origin.cpp"), str(host),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF",
        "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *build.LIBS], tmp_path)
    paths, expected = [], []
    for index, data in enumerate(corpus()):
        path = tmp_path / f"case-{index}.ghost"
        path.write_bytes(data)
        paths.append(str(path))
        try:
            decode_clip(data)
        except ClipError:
            expected.append(0)
        else:
            expected.append(1)
    result = subprocess.run([str(target)], input="\n".join(paths) + "\n", cwd=tmp_path,
        capture_output=True, text=True, timeout=30, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}, \
        "codec or native playback source changed during the witness"
    actual = [int(value) for value in result.stdout.splitlines()]
    receipt = {"cases": len(expected), "accepted": sum(expected), "native": actual,
               "python": expected, "source_sha256": hashes,
               "executable_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    (tmp_path / "codec-parity.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    assert set(expected) == {0, 1}
    assert actual == expected, [(index, want, got) for index, (want, got)
                                in enumerate(zip(expected, actual, strict=False)) if want != got]

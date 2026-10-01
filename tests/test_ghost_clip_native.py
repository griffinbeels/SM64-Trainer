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

from ghost_native_fixture import frame, producer
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.clip import ClipError, SEGMENT_CLOCK, STAR_CLOCK, decode_clip, encode_clip, hidden_refusals
from sm64_events.ghost.native_layout import (
    CAMERA_FOV_AT, HEADER_BYTES as H, HELD_AT, HELD_RELEASED_AT, PAD_AT, PARTICLE_BYTES, PARTICLE_OPACITY_AT,
    PARTICLE_TRANSLATION_AT, PARTICLES_AT, PARTICLES_ENTRIES_AT, POSE_AT, RECORD_BYTES as R, REFUSAL_AT,
    ROOT_KIND_AT, SHADOW_AT, SHADOW_RESERVED_AT, SOUND_AT, SOUND_BITS_AT,
)
from test_ghost_clip import BAD_CLOCK_WORDS, BAD_HEADER_WORDS, REAL_GCLIP5, as_gclip5, as_gclip6, encoded, matrix_frame

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = Path(os.environ.get("SM64_GHOST_PLAYBACK_ROOT", ROOT)).resolve()

REAL_GCLIP4 = Path(__file__).parent / "fixtures/attempt_2549_whomps-fortress_shoot-into-the-wild-blue_0m10s66.ghost"


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
        struct.pack_into("<I", changed, H + offset, value)
        values.append(bytes(changed))
    for status in range(12):
        values.append(encoded((frame(world=status),)))
        # Timing and origin are unchanged; absent pose remains a refusal even
        # when the historical renderer was ready or its target was unsupported.
        values.append(base[:H] + frame(world=status, valid=29))
    for offset, value in [*BAD_HEADER_WORDS, *((H + at, value) for at, value in BAD_CLOCK_WORDS),
                           (H, 192), (H + 4, 1), (H + 20, 2), (H + 28, 63),
                           (H + 12, 12),
                           (H + 72, 0), (H + POSE_AT, 0x7fc00000), (H + CAMERA_FOV_AT, 0x7f800000)]:
        changed = bytearray(base)
        struct.pack_into("<I", changed, offset, value)
        values.append(bytes(changed))
    for offset, value in [(32, 1), (40, 101), (48, 999), (56, 99), (308, 0), (316, 10)]:
        changed = bytearray(encoded((frame(1), frame(2))))
        struct.pack_into("<Q" if offset in (32, 40, 48, 308, 316) else "<I", changed, H + R + offset, value)
        values.append(bytes(changed))
    # Pose refusal reasons: kept on refused rows and on carried roots; an
    # unknown reason or any other pose byte on a refusal is refused.
    for reason in (1, 3, 11, 19, 20, 23):
        values.append(encoded((frame(1, valid=29, pose_refusal=reason), frame(2))))
    values.append(encoded((frame(1, pose_refusal=19),)))
    for offset, value in ((H + REFUSAL_AT, 24), (H + POSE_AT, 1)):
        changed = bytearray(encoded((frame(1, valid=29, pose_refusal=19), frame(2))))
        changed[offset] = value
        values.append(bytes(changed))
    values.extend(pad_cases())
    values.extend(held_cases())
    # Recorded shadow and sound: accepted shapes, then each native refusal.
    evidence = encoded((frame(1, shadow=True, sounds=(0x24228080,), particles=((0x0401DD60, (1, 2, -300)),)),
                        frame(2, sounds=tuple(range(1, 9)), overflow=True, particles=())))
    values.append(evidence)
    for offset, pack, value in [(H + ROOT_KIND_AT, "<I", 1), (H + SHADOW_AT + 60, "<f", 0.5),
                                (H + SHADOW_AT, "<I", 0x7fc00000), (H + SHADOW_AT + SHADOW_RESERVED_AT, "<B", 1),
                                (H + SOUND_AT, "<B", 9), (H + R + SOUND_AT + 1, "<B", 2),
                                (H + SOUND_AT + 1, "<B", 1), (H + SOUND_AT + SOUND_BITS_AT + 4, "<I", 5),
                                (H + R + SHADOW_AT + 8, "<I", 1), (H + R + SOUND_AT + SOUND_BITS_AT, "<I", 0),
                                (H + PARTICLES_AT, "<B", 17), (H + PARTICLES_AT + 1, "<B", 2), (H + PARTICLES_AT + 2, "<B", 1),
                                (H + PARTICLES_AT + PARTICLES_ENTRIES_AT + PARTICLE_OPACITY_AT, "<h", 1), (H + PARTICLES_AT + PARTICLES_ENTRIES_AT + PARTICLE_TRANSLATION_AT, "<I", 0x7fc00000),
                                (H + PARTICLES_AT + PARTICLES_ENTRIES_AT + PARTICLE_BYTES, "<I", 0x0401DD60), (H + R + PARTICLES_AT, "<B", 1)]:
        changed = bytearray(evidence)
        struct.pack_into(pack, changed, offset, value)
        values.append(bytes(changed))
    # A star clip may carry its dance tail; a segment clip may not.
    values.append(encode_clip((frame(1), frame(2)), producer(), clock_kind=STAR_CLOCK, dance_tail=True))
    segment_tail = bytearray(encoded((frame(),), SEGMENT_CLOCK))
    struct.pack_into("<I", segment_tail, 124, 1)
    values.append(bytes(segment_tail))
    values.extend(refusal_runs())
    # A real GCLIP4 recording saved before particles plays; a broken one does not.
    saved = REAL_GCLIP4.read_bytes()
    values.append(saved)
    for offset, value in ((20, 321), (H, 653), (H + 20, 637), (H + 28, 128)):
        broken = bytearray(saved)
        struct.pack_into("<I", broken, offset, value)
        values.append(bytes(broken))
    return values


def pad_cases():
    cases = [encoded((frame(pad=(0, 0, 0)),)), encoded((frame(pad=(-128, 127, 0xff3f)),)),
             encoded((frame(1, valid=29, pose_refusal=3, pad=(10, -20, 0x8000)), frame(2))),
             encoded((frame(1, pad=(-84, 72, 0x8011)), frame(2), frame(3, pad=(0, 0, 0))))]
    for value in ((-129, 0, 0), (128, 0, 0), (0, -129, 0), (0, 128, 0), (0, 0, 0x0080), (0, 0, 0x0040)):
        cases.append(encoded((frame(),))[:H] + frame(pad=value))
    stray = bytearray(encoded((frame(),)))
    stray[H + PAD_AT] = 1
    cases.append(bytes(stray))
    reserved = bytearray(encoded((frame(pad=(0, 0, 0)),)))
    reserved[H + PAD_AT + 6] = 1
    cases.append(bytes(reserved))
    old = as_gclip6(encoded((frame(1, held=(0x13003174, 1, 12)), frame(2))))
    cases.extend((old, old[:-1]))
    forged = bytearray(old)
    struct.pack_into("<I", forged, H + 28, 31 | 256 | 512)
    cases.append(bytes(forged))
    return cases


def held_cases():
    """The held block, in his hand and released, and a GCLIP5 file from before release."""
    cases = []
    # A held object: accepted on a pose; refused without one, outside
    # segment 0x13, or as stray bytes on a record without the bit.
    cases.append(encoded((frame(1, held=(0x13003174, 1, 12)), frame(2))))
    # A box: no animation, id -1 at frame 0 only.
    cases.append(encoded((frame(1, held=(0x13004218, -1, 0)), frame(2))))
    for offset, value in ((H + HELD_AT + 6, 3), (H + HELD_AT + 4, 0xfe)):  # frame 3; id -2
        changed = bytearray(encoded((frame(1, held=(0x13004218, -1, 0)), frame(2))))
        changed[offset] = value
        cases.append(bytes(changed))
    for offset, value in ((H + HELD_AT + 3, 0x80), (H + HELD_AT + 30, 0)):
        changed = bytearray(encoded((frame(1, held=(0x13003174, 1, 12)), frame(2))))
        changed[offset] = value
        cases.append(bytes(changed))
    stray = bytearray(encoded((frame(1), frame(2))))
    stray[H + HELD_AT] = 1
    cases.append(bytes(stray))
    # Released: its own angle; in his hand none, and released is 0 or 1.
    thrown = encoded((frame(1, held=(0x13003174, 1, 12, 0x1234)), frame(2)))
    cases.append(thrown)
    for offset, value in ((H + HELD_AT + HELD_RELEASED_AT, 0), (H + HELD_AT + HELD_RELEASED_AT, 2)):
        changed = bytearray(thrown)
        changed[offset] = value
        cases.append(bytes(changed))
    # A GCLIP5 file (GC6 records, before released objects) plays; a torn one does not.
    older = as_gclip5(encoded((frame(1, held=(0x13003174, 1, 12)), frame(2))))
    cases.extend((older, older[:-1], older[:8] + b"\x04" + older[9:]))
    # His real one: Behind Chain Chomp's Gate, a Bob-omb in his hand for 85 frames.
    cases.append(REAL_GCLIP5.read_bytes())
    return cases


def refusal_runs():
    """Refused runs playback bridges or hides: the picker's blink count."""
    def clip(refused, kind=STAR_CLOCK, rows=range(1, 7), changed=None):
        changed = changed or {}
        return encode_clip(tuple(frame(i, **({"valid": 29, "pose_refusal": 10} if i in refused else {}),
                                       **changed.get(i, {})) for i in rows), producer(), clock_kind=kind)
    later = {"origin": 2}
    return [clip({3}), clip({3, 4}), clip({3, 4, 5}), clip({3, 4}, SEGMENT_CLOCK), clip({3, 4, 5}, SEGMENT_CLOCK),
            clip({1}), clip({6}), clip({2, 5}),
            clip({3}, changed={4: {"area": 2}}), clip({3}, changed={3: {"area": 2}}),  # another scene
            clip({3}, changed={i: {"elapsed": i + 1} for i in (4, 5, 6)}),  # the clock skipped a frame
            clip({2}, rows=(1, 2, 4)),  # an occurrence never arrived
            clip({3}, changed={5: later, 6: later}), clip({5, 6}, changed={5: later, 6: later})]  # past the index


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
    inputs = [source, native / "ghost_origin.cpp", native / "ghost_annotation_assets.cpp", host, *(native / name for name in (
        "ghost_clip.h", "ghost_clip_upgrade.h", "ghost_capture.h", "ghost_playback.h", "ghost_origin.h",
        "ghost_clock.h", "ghost_pose.h", "ghost_annotation.h", "ghost_annotation_assets.h")),
        COMPONENTS / "renderer/model/model.h", COMPONENTS / "tools/build_plugin.py",
        *(ROOT / name for name in ("src/sm64_events/ghost/clip.py", "tests/ghost_native_fixture.py",
                                   "tests/test_ghost_clip.py", "tests/test_ghost_clip_native.py"))]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    build._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        f"/I{native}", f"/I{COMPONENTS / 'renderer/model'}", str(source), str(native / "ghost_origin.cpp"),
        str(native / "ghost_annotation_assets.cpp"), str(host),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF",
        "/MANIFEST:EMBED", "/MANIFESTUAC:level='asInvoker'", *build.LIBS], tmp_path)
    paths, expected = [], []
    for index, data in enumerate(corpus()):
        path = tmp_path / f"case-{index}.ghost"
        path.write_bytes(data)
        paths.append(str(path))
        try:
            clip = decode_clip(data)
        except ClipError:
            expected.append((0, 0))
        else:
            expected.append((1, hidden_refusals(clip)))
    result = subprocess.run([str(target)], input="\n".join(paths) + "\n", cwd=tmp_path,
        capture_output=True, text=True, timeout=30, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    assert hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}, \
        "codec or native playback source changed during the witness"
    actual = [tuple(int(value) for value in line.split()) for line in result.stdout.splitlines()]
    receipt = {"cases": len(expected), "accepted": sum(accepted for accepted, _ in expected), "native": actual,
               "python": expected, "source_sha256": hashes,
               "executable_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    (tmp_path / "codec-parity.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    assert {accepted for accepted, _ in expected} == {0, 1}
    assert [accepted for accepted, _ in expected[-5:]] == [1, 0, 0, 0, 0], "a saved GCLIP4 plays; broken ones do not"
    runs = len(refusal_runs())
    assert [hidden for _, hidden in expected[-5 - runs:-5]] == [0, 0, 3, 0, 3, 1, 1, 0, 1, 1, 1, 1, 0, 0], \
        "a short refused run between poses in one scene is held; anything else blinks"
    assert actual == expected, [(index, want, got) for index, (want, got)
                                in enumerate(zip(expected, actual, strict=False)) if want != got]

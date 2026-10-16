"""Native clock parity and confidence guards, separate from the browser feed."""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors import counter_epoch as E
from sm64_events.detectors.anchors import IGT_WRAP_CEILING, NEAR_ZERO_IGT
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory import addresses as A

ROOT = Path(__file__).resolve().parents[1]
FIELDS = "valid refusal transition raw bank progress origin leg elapsed continuity".split()


@pytest.fixture(scope="module")
def native_clock(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("native MSVC clock witness")
    work = tmp_path_factory.mktemp("native_ghost_clock")
    spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC toolchain is required for this explicit native witness"
    target = work / "ghost_clock_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{ROOT / 'plugin/gfxwrap'}", str(ROOT / "plugin/gfxwrap/ghost_clock.cpp"),
        str(ROOT / "tests/native/ghost_clock_host.cpp"), f"/Fe:{target}", f"/Fo{work}\\",
        "/link", *compiler.LIBS], work)
    castles, warps = sorted(A.CASTLE_LEVELS), sorted(E.AREA_WARP_OPS)
    config = [NEAR_ZERO_IGT, E.AREA_LOAD_WINDOW, E.TELEPORT_WINDOW, E.LEVEL_LOAD_TAIL_FRAMES,
              E.WARP_FIRED_WINDOW, A.ACT_TELEPORT_FADE_OUT, IGT_WRAP_CEILING,
              len(castles), len(warps), *castles, *warps]

    def run(rows):
        payload = " ".join(map(str, config)) + "\n"
        for row in rows:
            payload += row + "\n" if isinstance(row, str) else "S " + " ".join(map(str, row)) + "\n"
        result = subprocess.run([str(target)], input=payload, cwd=work, capture_output=True,
                                text=True, timeout=10, check=False, **quiet_spawn_kwargs())
        assert result.returncode == 0, result.stdout + result.stderr
        lines = result.stdout.splitlines()
        counts = lines.pop().split()
        assert counts[0] == "@" and counts[2:] == ["0", "0"], result.stdout
        outputs = [dict(zip(FIELDS, map(int, line.split()), strict=True)) for line in lines]
        assert int(counts[1]) == len(outputs)
        return outputs
    return run


def sample(frame, raw, *, sequence=None, epoch=1, level=24, area=1, action=1,
           warp=0, timer=0, origin=False, fields=7):
    seq = frame + 1 if sequence is None else sequence
    return [fields, epoch, seq, frame, raw, level, area, action, warp, timer,
            epoch if origin else 0, seq if origin else 0]


def snapshot(row):
    return GameSnapshot(wall_time_utc=datetime(2026, 9, 21, tzinfo=timezone.utc),
        global_timer=row[3], mario_action=row[7], mario_action_timer=0, num_stars=0,
        last_completed_course=0, last_completed_star=0, igt_overall=row[4],
        curr_level=row[5], curr_area=row[6], pending_warp_op=row[8], delayed_warp_timer=row[9])


def test_classifier_parity_with_real_python_owners(native_clock):
    prefix = [sample(i, i, origin=i == 0) for i in range(101)]
    cases = [
        prefix + [sample(101, 101, area=2), sample(102, 0, area=2), sample(103, 1, area=2),
                  sample(104, 1, area=2), sample(105, 2, area=2, warp=A.WARP_OP_WARP_DOOR, timer=1),
                  sample(106, 3, area=2, warp=A.WARP_OP_WARP_DOOR, timer=0), sample(107, 0), sample(108, 1)],
        prefix + [sample(101, 101, warp=A.WARP_OP_WARP_DOOR, timer=1), sample(102, 102), sample(103, 0)],
        prefix + [sample(101, 101, action=A.ACT_TELEPORT_FADE_OUT), sample(102, 0), sample(103, 1)],
        prefix + [sample(101, 101, level=9, area=3), sample(102, 102, level=9, area=2),
                  sample(103, 0, level=9, area=2), sample(104, 1, level=9, area=2)],
        [sample(i, i, level=6, origin=i == 0) for i in range(101)]
        + [sample(101, 101, level=6, area=2), sample(102, 0, level=6, area=2)],
    ]
    for edge in (1 + E.LEVEL_LOAD_TAIL_FRAMES, 2 + E.LEVEL_LOAD_TAIL_FRAMES):
        cases.append([sample(0, 0, origin=True),
                      *[sample(i, i - 1, level=9) for i in range(1, edge)],
                      sample(edge, edge - 1, level=9, area=2),
                      sample(edge + 1, 0, level=9, area=2)])
    for extra in (0, 1):
        zero = 101 + E.TELEPORT_WINDOW + extra
        cases.append(prefix + [sample(101, 101, action=A.ACT_TELEPORT_FADE_OUT),
                               *[sample(i, i) for i in range(102, zero)], sample(zero, 0)])
        zero = 101 + E.AREA_LOAD_WINDOW + extra
        cases.append(prefix + [*[sample(i, i, area=2) for i in range(101, zero)],
                               sample(zero, 0, area=2)])
        zero = 102 + E.WARP_FIRED_WINDOW + extra
        cases.append(prefix + [sample(101, 101, warp=A.WARP_OP_WARP_DOOR, timer=1),
                               sample(102, 102, warp=A.WARP_OP_WARP_DOOR, timer=0),
                               *[sample(i, i) for i in range(103, zero)], sample(zero, 0)])
    for rows in cases:
        outputs = native_clock(["N", *rows])
        clock, classifier, previous = IgtClock(), E.EpochTracker(), None
        for row, out in zip(rows, outputs, strict=True):
            current = snapshot(row)
            transition = 2 if row[10] else 1
            if previous is not None:
                classifier.observe(previous, current)
                if current.curr_level != previous.curr_level:
                    transition = 3
                if current.igt_overall < previous.igt_overall and current.igt_overall <= NEAR_ZERO_IGT:
                    area, teleport = classifier.is_area_load(current), classifier.is_teleport(current)
                    transition = 5 if area else 6 if teleport else 3 if transition == 3 else 4
                    classifier.restarted(current, previous.igt_overall)
            clock.observe(current)
            assert out["transition"] == transition, (row, out)
            if out["valid"] & 2:
                assert out["bank"] == clock.banked_frames() == classifier.banked
                assert out["progress"] == clock.banked_frames() + current.igt_overall
            assert out["valid"] & 4, (row, out)  # complete traces preserve elapsed continuity
            previous = current
        assert outputs[-1]["valid"] & 2


def test_guarded_origin_gaps_rewind_and_duplicate(native_clock):
    rows = [sample(0, 50), sample(1, 0), sample(2, 1),
            sample(3, 0, origin=True), sample(4, 1), sample(4, 1),
            sample(5, 2, sequence=7), sample(6, 0, sequence=8, origin=True),
            sample(7, 1, sequence=9, fields=1), sample(8, 2, sequence=10),
            sample(1, 3, sequence=11), sample(2, 0, sequence=12, origin=True),
            sample(4, 2, sequence=13), sample(5, 1, sequence=14, origin=True)]
    out = native_clock(rows)
    assert not out[0]["valid"] & 2 and out[0]["valid"] & 4  # unknown bank, valid relative reference
    assert not out[1]["valid"] & 2  # a just-attached tracker lacks old warp recencies
    assert out[1]["origin"] > out[0]["origin"]  # identity survives unknown bank
    assert out[1]["bank"] == out[1]["progress"] == 0
    assert out[3]["valid"] & 2 and out[3]["progress"] == 0
    assert out[5]["refusal"] == 16 and not out[5]["valid"]  # duplicate cannot bank twice
    assert out[6]["refusal"] & 4 and out[6]["continuity"] != out[4]["continuity"]
    assert out[8]["refusal"] == 2 and not out[8]["valid"]
    assert out[9]["valid"] & 4 and not out[9]["valid"] & 2
    assert out[10]["refusal"] & 8 and not out[10]["valid"] & 2
    assert out[11]["origin"] > out[7]["origin"]
    assert out[12]["refusal"] & 512 and out[12]["continuity"] != out[11]["continuity"]
    assert out[13]["refusal"] == 32  # exact marker cannot pretend a nonzero raw counter is zero


def test_elapsed_mid_leg_levels_deferred_zero_and_retry(native_clock):
    rows = [sample(0, 50), sample(1, 51), sample(2, 52, level=6),
            sample(3, 53, level=6), sample(4, 0, level=6), sample(5, 1, level=6),
            sample(6, 2, level=9), sample(7, 0, level=9), sample(8, 1, level=9),
            sample(9, 0, level=9), sample(10, 1, level=9)]
    out = native_clock(rows)
    assert [row["elapsed"] for row in out] == [0, 1, 2, 3, 3, 4, 5, 5, 6, 6, 7]
    assert len({row["continuity"] for row in out}) == 1
    assert all(row["valid"] & 4 for row in out)
    # The later segment can subtract its observed origin without inheriting
    # the whole-star bank, and a retry does not impose SegmentEngine policy.
    assert out[-1]["elapsed"] - out[5]["elapsed"] == 3
    assert out[-1]["origin"] != out[8]["origin"]


def test_stale_older_epoch_cannot_replace_newer_occurrence(native_clock):
    rows = [sample(100, 0, sequence=10, epoch=2, origin=True),
            sample(99, 0, sequence=9, epoch=1, origin=True),
            sample(101, 1, sequence=11, epoch=2),
            sample(102, 2, sequence=12, epoch=2, fields=1),
            sample(99, 0, sequence=9, epoch=1, origin=True),
            sample(103, 3, sequence=13, epoch=2)]
    out = native_clock(rows)
    assert out[1]["refusal"] == 16 and not out[1]["valid"]
    assert out[2]["valid"] == 7 and out[2]["progress"] == 1
    assert out[2]["elapsed"] == 1
    assert out[2]["origin"] == out[0]["origin"]
    assert out[2]["continuity"] == out[0]["continuity"]
    assert out[3]["refusal"] == 2 and not out[3]["valid"]
    assert out[4]["refusal"] == 16 and not out[4]["valid"]
    assert not out[5]["valid"] & 2 and out[5]["valid"] & 4
    assert out[5]["continuity"] != out[2]["continuity"]


def test_discontinuity_wrap_reset_and_exhaustion(native_clock):
    rows = [sample(0, 0, origin=True), sample(1, 100), sample(2, 50),
            sample(3, 51), "R", sample(4, 52), sample(5, 0, origin=True),
            sample(6, 65535), sample(7, 0), "N", sample(0, 0, origin=True),
            "O", sample(1, 0, origin=True), sample(2, 1),
            "N", sample(0, 0, origin=True), "L", sample(1, 0, origin=True),
            "N", sample(0, 0, origin=True), "E", sample(1, 1),
            "N", "C", sample(0, 50)]
    out = native_clock(rows)
    assert out[2]["refusal"] & 64 and out[2]["continuity"] != out[1]["continuity"]
    assert out[4]["continuity"] != out[3]["continuity"]
    assert out[5]["origin"] > out[0]["origin"]
    assert out[7]["refusal"] & 128 and not out[7]["valid"] & 2
    for index in (9, 10, 12, 14, 15):
        assert out[index]["refusal"] & 256 and not out[index]["valid"] & (2 | 4)

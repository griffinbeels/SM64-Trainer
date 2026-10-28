"""The attempt cutter on the REAL live spool: the offline producer (tap_offline_host --live, through
scene_tap.cpp and scene_spool.cpp) records while a lease is held, live_spool.Spool.follow feeds the
cutter's take, and the scene is ready, growing, while the producer is still recording.

test_scene_live_spool.py's synthetic frames: game frames 1000..1005, area 1 for the first three
and area 2 after, so an attempt anchored at 1000 and completed at 1001 ends its scene at the
last picture before area 2. Skipped (not failed) on a machine without the x86 MSVC toolchain."""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import attempt_cutter  # noqa: E402
import attempt_span  # noqa: E402
import build_plugin  # noqa: E402
import live_spool  # noqa: E402
import package as pk  # noqa: E402
import scene_session  # noqa: E402

spec = importlib.util.spec_from_file_location("scene_live_spool_tests", ROOT / "tests/test_scene_live_spool.py")
spool_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spool_tests)

pytestmark = pytest.mark.skipif(not build_plugin.toolchain_available(), reason="no x86 MSVC toolchain (vcvars32.bat)")


def _until(brain, test, seconds=60.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        brain.tick()
        found = test()
        if found:
            return found
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_a_take_follows_the_live_spool_and_its_scene_is_ready_while_still_recorded(tmp_path):
    rom = spool_tests._rom()
    frames = spool_tests._frames(tmp_path / "frames", rom)
    lab = spool_tests._lab(tmp_path / "live")
    lease = live_spool.Lease(lab)
    lease.start()
    host = spool_tests.Host(tmp_path / "live", frames[0])
    events: list[dict] = []
    brain = attempt_cutter.Brain(rom, pk.provenance.segment_loads(rom), tmp_path / "attempts",
                                 lambda: live_spool.open_spool(lab), events.append)
    try:
        assert host.send("poke") == "ok 1"
        for frame in frames[:3]:
            host.send(f"frame {frame}")
            host.send(f"vi {frame}")
        host.send("poke")
        refs = _until(brain, lambda: (brain.spool or live_spool.open_spool(lab)) and len(
            live_spool.open_spool(lab).tasks()) >= 2 and live_spool.open_spool(lab).tasks())
        anchor = refs[0]
        brain.note({"op": "start", "kind": "anchor", "frame": anchor.global_timer, "utc": anchor.utc.isoformat()})
        seat = _until(brain, lambda: brain.takes and brain.takes[0]).seats[0]
        brain.note({"op": "complete", "attempt": attempt_span.AttemptMark(
            7, anchor.global_timer, refs[1].global_timer, anchor.utc, refs[1].utc, "success").as_json()})
        growing = _until(brain, lambda: next((event for event in events if event.get("id") == 7), None))
        assert growing["state"] == "ready" and growing["growing"] is True and growing["path"] == str(seat.path)
        published = _until(brain, lambda: seat.grow.is_file() and scene_session.Growth(seat.path, seat.grow).read(rom))
        assert len(published) >= 1                               # the scene opens while the producer records
        for frame in frames[3:5]:                                # area 2: the next place appears
            host.send(f"frame {frame}")
            host.send(f"vi {frame}")
        host.send("poke")
        ready = _until(brain, lambda: next((event for event in events if event.get("id") == 7
                                            and event.get("growing") is False), None))
        assert (ready["state"], ready["why"], ready["frames"]) == ("ready", "fade", 3)
        assert ready["presents_qpc"] and ready["qpc_frequency"] == live_spool.open_spool(lab).frequency
        cut = pk.Package(Path(ready["path"]).read_bytes(), rom)
        expected = tmp_path / "expected.sm64scene"
        pk.write_package(live_spool.open_spool(lab).source(anchor.index, anchor.index + 2), rom, expected, pk.ZLIB)
        whole = pk.Package(expected.read_bytes(), rom)
        for index in range(3):
            assert cut.frame(index) == whole.frame(index)
            assert pk.state_fingerprint(*cut.scene_state(index)[1:]) == pk.state_fingerprint(
                *whole.scene_state(index)[1:])
        assert not seat.grow.exists()
    finally:
        brain.close()
        lease.stop()
        host.close()

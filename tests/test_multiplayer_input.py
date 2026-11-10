"""Actual Python LiveSeat transition goldens vs native owned-snapshot input.

Root serializes the native fixture's MSVC lease. The ledger/golden construction
test runs without compiling, opening graphics or touching a live source.
"""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from ghost_native_fixture import frame
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.multiplayer.live_state import LiveSeat
from sm64_events.multiplayer.protocol import PoseError, encode_pose

ROOT = Path(__file__).resolve().parents[1]
INCARNATION = bytes(range(16))
OTHER = b"b" * 16
AUTHORIZE, REVOKE, CLEAR, INGEST, PUBLISH, CONSUME = range(1, 7)


def packet(sequence=1, *, source=1, continuity=1, elapsed=0, incarnation=INCARNATION,
           configuration=4, epoch=5, rom=1, rom_generation=3, warp=None, hidden=False, **values):
    raw = bytearray(frame(sequence, configuration=configuration, epoch=epoch, **values))
    raw[152] = int(hidden)  # independent gc_pose.hidden offset in the portable native record
    if values.get("valid", 31) & 2:
        struct.pack_into("<H", raw, 112, sequence & 0xffff)  # actual capture's fresh animTimer
    if warp is not None:
        struct.pack_into("<Hh", raw, 256, *warp)
    return encode_pose(raw, participant_incarnation=incarnation, source_generation=source,
        elapsed_ns=elapsed, continuity=continuity, rom_layout=rom, rom_generation=rom_generation)


def ledger(operations):
    return b"".join(struct.pack("<IIQI", op, seat, now, len(data)) + data
                    for op, seat, now, data in operations)


def observed(seat, authorized, now):
    # This is the actual Python implementation, not a retyped fence algorithm.
    latest = seat.latest(now_ns=now) if seat is not None else None
    pose = latest.pose if latest is not None else None
    return {"authorized": int(authorized), "present": int(latest is not None),
        "visible": int(pose is not None and pose.frame.playable and not pose.frame.pose_hidden),
        "current": latest is not None,
        "sequence": pose.frame.sequence if pose else 0, "source": pose.source_generation if pose else 0,
        "continuity": pose.continuity if pose else 0, "elapsed": pose.elapsed_ns if pose else 0,
        "received": latest.received_ns if latest else 0}


class Golden:
    def __init__(self):
        self.operations, self.expectations, self.acceptance = [], {}, {}
        self.seat = LiveSeat(INCARNATION, stale_after_ns=50)
        self.authorized = True

    def op(self, kind, now=0, data=b""):
        self.operations.append((kind, 0, now, data))

    def receive(self, data, now):
        try:
            self.seat.ingest(data, received_ns=now)
            accepted = True
        except PoseError:
            accepted = False
        self.op(INGEST, now, data)
        self.acceptance[len(self.operations) - 1] = accepted

    def observe(self, now, *, publish=True):
        if publish:
            self.op(PUBLISH, now)
        self.op(CONSUME, now)
        self.expectations[len(self.operations) - 1] = observed(self.seat, self.authorized, now)


def golden_expiry(g):
    g.op(AUTHORIZE, data=INCARNATION)
    g.observe(0)
    g.receive(packet(9, source=2, elapsed=100), 10)
    g.op(AUTHORIZE, 10, INCARNATION)  # identical active control preserves the actual Python object
    g.observe(10)
    g.observe(60, publish=False)
    g.observe(61, publish=False)
    g.receive(packet(900, source=1, elapsed=900), 70)
    g.receive(packet(9, source=2, elapsed=100), 70)
    g.observe(71)
    g.receive(packet(10, source=2, elapsed=100), 90)
    g.observe(90)
    g.receive(packet(11, source=2, configuration=5, elapsed=101), 95)
    g.receive(packet(11, source=2, elapsed=99), 95)
    g.observe(95)


def golden_clear_and_restart(g):
    g.receive(packet(11, source=2, continuity=3, elapsed=101), 100)
    g.observe(100)
    g.seat.clear()
    g.op(CLEAR, 100)
    g.observe(100, publish=False)
    g.receive(packet(11, source=2, continuity=4, elapsed=102), 110)
    g.receive(packet(12, source=2, continuity=2, elapsed=102), 110)
    g.observe(110)
    g.receive(packet(1, source=3, epoch=6), 120)
    g.observe(120)
    g.receive(packet(999, source=2, continuity=99, elapsed=999), 130)
    g.receive(packet(2, source=3, epoch=6, elapsed=1, incarnation=OTHER), 130)
    g.receive(packet(2, source=3, epoch=6, elapsed=1), 119)
    g.observe(130)
    g.receive(packet(2, source=3, epoch=6, continuity=2, elapsed=2, valid=29, pose_refusal=3, pad=None), 140)
    g.observe(140)
    g.receive(packet(3, source=3, epoch=6, continuity=3, elapsed=3), 150)
    g.observe(150)


def golden_revocation(g):
    g.seat.revoke()
    g.authorized = False
    g.op(REVOKE, 151)
    g.op(AUTHORIZE, 151, INCARNATION)
    g.receive(packet(4, source=999), 151)
    g.observe(151)
    g.seat = LiveSeat(OTHER, stale_after_ns=50)
    g.authorized = True
    g.op(AUTHORIZE, 160, OTHER)
    g.receive(packet(incarnation=OTHER), 160)
    g.observe(160)
    g.receive(packet(2, source=999), 161)
    g.observe(210, publish=False)
    g.observe(211, publish=False)


def transition_golden():
    golden = Golden()
    golden_expiry(golden)
    golden_clear_and_restart(golden)
    golden_revocation(golden)
    return golden.operations, golden.expectations, golden.acceptance


def hidden_golden():
    golden = Golden()
    golden.op(AUTHORIZE, data=INCARNATION)
    for sequence, hidden in enumerate((False, True, False), start=1):
        golden.receive(packet(sequence, hidden=hidden), sequence)
        golden.observe(sequence)
    return golden.operations, golden.expectations, golden.acceptance


def test_python_transition_ledger_has_real_acceptance_and_expiry_evidence():
    operations, expectations, acceptance = transition_golden()
    assert len(ledger(operations)) < 100_000
    assert len(expectations) >= 15 and any(acceptance.values()) and not all(acceptance.values())
    assert list(expectations.values())[0]["present"] == 0
    assert any(value["present"] and not value["visible"] for value in expectations.values())
    assert list(expectations.values())[-1]["present"] == 0


def test_python_hidden_golden_preserves_present_pose_but_withdraws_visibility():
    operations, expectations, acceptance = hidden_golden()
    datagrams = [data for kind, _, _, data in operations if kind == INGEST]
    assert [data[200] for data in datagrams] == [0, 1, 0]  # 64-byte wire header + record offset152 - prefix16
    assert all(acceptance.values())
    views = list(expectations.values())
    assert [view["visible"] for view in views] == [1, 0, 1]
    assert all(view["present"] and view["current"] for view in views)
    assert [view["sequence"] for view in views] == [1, 2, 3]


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("MSVC native worker/render input witness")
    work = tmp_path_factory.mktemp("multiplayer_input")
    spec = importlib.util.spec_from_file_location("input_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    vcvars = compiler.find_vcvars32()
    assert vcvars, "x86 MSVC is required for the owned native input witness"
    target = work / "ghost_live_input_host.exe"
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17",
        f"/I{ROOT / 'plugin/gfxwrap'}", f"/I{ROOT / 'renderer/model'}",
        str(ROOT / "plugin/gfxwrap/ghost_live_codec.cpp"), str(ROOT / "plugin/gfxwrap/ghost_live_input.cpp"),
        str(ROOT / "tests/native/ghost_live_input_host.cpp"),
        f"/Fe:{target}", f"/Fo{work}\\", "/link", "/MANIFEST:EMBED",
        "/MANIFESTUAC:level='asInvoker'", *compiler.LIBS], work)
    return target


def run(native, *args):
    result = subprocess.run([str(native), *map(str, args)], cwd=native.parent, capture_output=True,
                            text=True, timeout=30, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line]


def script(native, tmp_path, operations, timeout=50):
    path = tmp_path / "control-and-wire.bin"
    path.write_bytes(ledger(operations))
    return run(native, "script", path, timeout)


def test_native_lifetime_transitions_match_actual_python_live_seat(native, tmp_path):
    operations, expectations, acceptance = transition_golden()
    reports = script(native, tmp_path, operations)
    assert len(reports) == len(operations)
    for index, accepted in acceptance.items():
        assert (reports[index]["result"] == "ok") == accepted, (index, reports[index])
    for index, expected in expectations.items():
        native_view = reports[index]["seats"][0]
        assert {key: native_view[key] for key in expected} == expected, (index, native_view, expected)
    assert all(reports[i]["revision"] <= reports[i + 1]["revision"] for i in range(len(reports) - 1))


def test_native_hidden_transitions_match_actual_python_live_seat(native, tmp_path):
    operations, expectations, acceptance = hidden_golden()
    reports = script(native, tmp_path, operations)
    assert len(reports) == len(operations)
    for index, accepted in acceptance.items():
        assert (reports[index]["result"] == "ok") == accepted, (index, reports[index])
    for index, expected in expectations.items():
        native_view = reports[index]["seats"][0]
        assert {key: native_view[key] for key in expected} == expected, (index, native_view, expected)


@pytest.mark.parametrize("boundary", ["refusal", "hidden", "source", "continuity", "scene", "warp", "clear", "revoke"])
def test_barriers_withdraw_adopted_geometry_before_new_publication(native, tmp_path, boundary):
    ops = [(AUTHORIZE, 0, 0, INCARNATION), (INGEST, 0, 1, packet()),
           (PUBLISH, 0, 1, b""), (CONSUME, 0, 1, b"")]
    changes = {"refusal": {"valid": 29, "pose_refusal": 3}, "hidden": {"hidden": True}, "source": {"source": 2},
        "continuity": {"continuity": 2}, "scene": {"level": 9}, "warp": {"warp": (1, 10)}}
    if boundary in changes:
        ops.append((INGEST, 0, 2, packet(2, elapsed=1, **changes[boundary])))
    else:
        ops.append((CLEAR if boundary == "clear" else REVOKE, 0, 2, b""))
    ops.append((CONSUME, 0, 2, b""))
    reports = script(native, tmp_path, ops)
    assert reports[3]["seats"][0]["current"] is True
    assert reports[4]["seats"][0]["current"] is False  # before consume/publication
    assert reports[5]["seats"][0]["present"] == 0


def test_refusal_barrier_survives_coalescing_to_a_later_visible_pose(native, tmp_path):
    ops = [(AUTHORIZE, 0, 0, INCARNATION), (INGEST, 0, 1, packet()),
           (PUBLISH, 0, 1, b""), (CONSUME, 0, 1, b""),
           (INGEST, 0, 2, packet(2, valid=29, pose_refusal=3)),
           (INGEST, 0, 3, packet(3)), (PUBLISH, 0, 3, b""), (CONSUME, 0, 3, b"")]
    reports = script(native, tmp_path, ops)
    assert reports[4]["seats"][0]["current"] is False and reports[5]["seats"][0]["current"] is False
    assert reports[-1]["seats"][0]["gate"] != reports[3]["seats"][0]["gate"]
    assert reports[-1]["seats"][0]["sequence"] == 3 and reports[-1]["seats"][0]["visible"] == 1


def test_hidden_barrier_survives_coalescing_to_a_later_visible_pose(native, tmp_path):
    ops = [(AUTHORIZE, 0, 0, INCARNATION), (INGEST, 0, 1, packet()),
           (PUBLISH, 0, 1, b""), (CONSUME, 0, 1, b""),
           (INGEST, 0, 2, packet(2, hidden=True)),
           (INGEST, 0, 3, packet(3)), (PUBLISH, 0, 3, b""), (CONSUME, 0, 3, b"")]
    reports = script(native, tmp_path, ops)
    assert reports[4]["seats"][0]["current"] is False and reports[5]["seats"][0]["current"] is False
    assert reports[-1]["seats"][0]["gate"] != reports[3]["seats"][0]["gate"]
    assert reports[-1]["seats"][0]["sequence"] == 3 and reports[-1]["seats"][0]["visible"] == 1


def test_clearing_one_seat_keeps_another_authorized_pose_current(native, tmp_path):
    ops = [(AUTHORIZE, 0, 0, INCARNATION), (AUTHORIZE, 1, 0, OTHER),
           (INGEST, 0, 1, packet()), (INGEST, 1, 1, packet(incarnation=OTHER)),
           (PUBLISH, 0, 1, b""), (CONSUME, 0, 1, b""), (CLEAR, 0, 2, b""), (CONSUME, 0, 2, b"")]
    report = script(native, tmp_path, ops)[-1]
    assert report["seats"][0]["present"] == 0 and report["seats"][0]["authorized"] == 1
    assert report["seats"][1]["present"] == 1 and report["seats"][1]["current"] is True


def test_zero_timeout_fails_closed_without_accepting_membership(native, tmp_path):
    ops = [(AUTHORIZE, 0, 0, INCARNATION), (INGEST, 0, 0, packet()),
           (PUBLISH, 0, 0, b""), (CONSUME, 0, 0, b"")]
    reports = script(native, tmp_path, ops, timeout=0)
    assert reports[0]["result"] == "exhausted" and reports[1]["result"] == "exhausted"
    assert all(not seat["authorized"] and not seat["present"] for seat in reports[-1]["seats"])


def test_countdown_and_sequence_loss_do_not_invent_barriers(native, tmp_path):
    ops = [(AUTHORIZE, 0, 0, INCARNATION), (INGEST, 0, 1, packet(warp=(1, 10))),
           (PUBLISH, 0, 1, b""), (CONSUME, 0, 1, b""),
           (INGEST, 0, 2, packet(100, warp=(1, 9))),
           (PUBLISH, 0, 2, b""), (CONSUME, 0, 2, b"")]
    reports = script(native, tmp_path, ops)
    assert reports[4]["seats"][0]["current"] is True
    assert reports[-1]["seats"][0]["gate"] == reports[3]["seats"][0]["gate"]
    assert reports[-1]["seats"][0]["sequence"] == 100


def test_reclaimed_ready_slots_never_overwrite_a_newer_adopted_revision(native, tmp_path):
    ops = [(AUTHORIZE, 0, 0, INCARNATION)]
    for sequence in range(1, 5):
        ops.extend([(INGEST, 0, sequence, packet(sequence)), (PUBLISH, 0, sequence, b"")])
    ops.append((CONSUME, 0, 4, b""))
    report = script(native, tmp_path, ops)[-1]
    assert report["seats"][0]["sequence"] == 4 and report["replaced"] == 1 and report["dropped"] == 0


def test_control_cannot_replace_active_identity_or_move_retired_nonce_to_another_seat(native, tmp_path):
    ops = [(AUTHORIZE, 0, 0, INCARNATION), (AUTHORIZE, 0, 0, OTHER),
           (AUTHORIZE, 1, 0, INCARNATION), (REVOKE, 0, 0, b""),
           (AUTHORIZE, 1, 0, INCARNATION), (AUTHORIZE, 0, 0, OTHER)]
    assert [row["result"] for row in script(native, tmp_path, ops)] == [
        "ok", "active_member", "retired_incarnation", "ok", "retired_incarnation", "ok"]


@pytest.mark.parametrize("seat", [3, 2**32 - 1])
def test_control_and_packets_are_bounded_to_three_seats(native, tmp_path, seat):
    ops = [(AUTHORIZE, seat, 0, INCARNATION), (INGEST, seat, 0, packet()),
           (CLEAR, seat, 0, b""), (REVOKE, seat, 0, b"")]
    assert all(row["result"] == "bad_seat" for row in script(native, tmp_path, ops))


def test_no_packet_or_unknown_admission_can_create_a_ready_pose(native, tmp_path):
    ops = [(INGEST, 0, 0, packet()), (AUTHORIZE, 0, 0, bytes(16)),
           (PUBLISH, 0, 0, b""), (CONSUME, 0, 0, b"")]
    reports = script(native, tmp_path, ops)
    assert reports[0]["result"] == "not_authorized" and reports[1]["result"] == "bad_incarnation"
    assert all(not seat["present"] and not seat["visible"] for seat in reports[-1]["seats"])


def test_native_nonce_tombstones_are_bounded_and_never_evicted(native):
    assert run(native, "capacity")[0]["history_capacity"] == 256


def test_receiver_room_scope_accepts_surviving_source_without_resetting_old_gates_or_history(native, tmp_path):
    path = tmp_path / "seed.bin"
    path.write_bytes(packet())
    report = run(native, "scoped", path)[0]
    assert report["scoped_admission"] is True and report["history_capacity"] == 256


def test_native_counter_exhaustion_withdraws_before_publication_and_never_wraps(native, tmp_path):
    path = tmp_path / "seed.bin"
    path.write_bytes(packet())
    assert run(native, "exhaustion", path)[0]["exhaustion"] is True


def test_actual_spsc_custody_stress_has_untorn_three_seat_values_and_truthful_counters(native, tmp_path):
    path = tmp_path / "seed.bin"
    path.write_bytes(packet())
    report = run(native, "stress", path)[0]
    assert report["stress"] is True and report["accepted"] == 30_000 and report["consumed"] > 0

"""The ghost copy's native producer (twin lane, channel v3).

With Ghosts in recording on, the clean recording never contains a ghost; a
second helper records the displayed picture beside it. These witnesses drive
the real delivery and channel natives. The twin block is read and written here
by the contract's byte offsets, independently of the C++ structs, so a layout
drift on either side fails here. The clean side uses the shipped client.
"""
import ctypes
import importlib.util
import json
import os
import queue
import secrets
import struct
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import gpuchannel
from test_gpu_cadence import Pipeline, cadence_host  # noqa: F401 (a fixture)
from test_gpu_delivery import Running, _build_host, eventually, offer_for, select

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin/gfxwrap"

# The wire, as the contract states it (twin-lane-spec I2).
TWIN_OFFSET_AT, TWIN_BYTES_AT, TWIN_NAMES_AT, RESERVED_AT, HEADER_BYTES = 840, 844, 848, 1360, 2048
TWIN_BYTES = 768
NONCE_AT, OFFER_COUNT_AT, DECISION_AT, PAYLOAD_AT = 48, 112, 156, 168  # gc_header, unchanged since v2
CLIENT, STATUS, BRIDGES, RECEIPTS = 0, 128, 256, 512
OFF, PREPARING, ACTIVE, STOPPED = 0, 1, 2, 3
CLIENT_OFF, CLIENT_READY = 0, 1
GD_CHANNEL_FAILED = 10009
GD_TWIN_SNAPSHOT_FAILED, GD_TWIN_IMPORT_FAILED, GD_TWIN_BRIDGE_FAILED = 10023, 10024, 10025
GD_TWIN_KEY_ABANDONED, GD_TWIN_UNAVAILABLE = 10026, 10027
U32 = ctypes.c_uint32


class TwinWire:
    """One process's view of a channel's header and twin block, by offset."""

    def __init__(self, nonce_lo, nonce_hi):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenFileMappingW.restype = ctypes.c_void_p
        kernel.OpenFileMappingW.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p)
        kernel.MapViewOfFile.restype = ctypes.c_void_p
        kernel.MapViewOfFile.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                         ctypes.c_uint32, ctypes.c_size_t)
        kernel.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        self.kernel = kernel
        name = f"Local\\SM64Trainer.GpuChannel.v1.{nonce_hi:016x}{nonce_lo:016x}"
        self.handle = kernel.OpenFileMappingW(0xF001F, False, name)
        assert self.handle, ctypes.get_last_error()
        self.view = kernel.MapViewOfFile(self.handle, 0xF001F, 0, 0, 0)
        assert self.view
        (self.seq, self.version, magic, _, self.total_bytes) = struct.unpack_from("<II8sII", self.raw(0, 24))
        assert magic == b"SM64GPC1" and self.seq and not self.seq & 1
        self.twin_offset, self.twin_bytes = struct.unpack_from("<II", self.raw(TWIN_OFFSET_AT, 8))
        self.nonce_lo, self.nonce_hi, self.epoch, self.generation = struct.unpack_from("<QQII", self.raw(NONCE_AT, 24))

    def raw(self, offset, length):
        return ctypes.string_at(self.view + offset, length)

    def u32(self, offset):
        return U32.from_address(self.view + offset).value

    def names(self, at):
        return tuple(self.raw(at + 256 * i, 256).decode("utf-16-le").split("\0", 1)[0] for i in range(2))

    def close(self):
        self.kernel.UnmapViewOfFile(self.view)
        self.kernel.CloseHandle(self.handle)

    def stable(self, offset, length):
        for _ in range(100):
            before = self.u32(offset)
            data = self.raw(offset, length)
            if not before & 1 and before == self.u32(offset):
                return data
        raise AssertionError("torn twin entry")

    def write(self, offset, body):
        """A client entry: seq odd while writing, +2 when done."""
        assert len(body) == 124
        seq = self.u32(offset)
        assert not seq & 1
        U32.from_address(self.view + offset).value = seq + 1
        ctypes.memmove(self.view + offset + 4, body, len(body))
        U32.from_address(self.view + offset).value = seq + 2

    def identity(self):
        return (self.nonce_lo, self.nonce_hi, self.epoch, self.generation)

    def request(self, lane):
        state = CLIENT_READY if lane else CLIENT_OFF
        self.write(self.twin_offset + CLIENT,
                   struct.pack("<IQQQII88x", state, lane, *self.identity()))

    def status(self):
        v = struct.unpack("<IIIIQQQQQQII56x", self.stable(self.twin_offset + STATUS, 128))
        return SimpleNamespace(seq=v[0], state=v[1], reason=v[2], reserved0=v[3], lane=v[4], resolved_pts=v[5],
                               copied=v[6], missed=v[7], identity=v[8:12])

    def bridges(self):
        out = []
        for i in range(2):
            v = struct.unpack("<IIQQQQQQQIIII48x", self.stable(self.twin_offset + BRIDGES + 128 * i, 128))
            if v[2]:
                out.append(SimpleNamespace(ready=v[1], token=v[2], occurrence=v[3], encode_serial=v[4], pts=v[5],
                                           duration=v[6], identity=v[7:11], texture_index=v[11], flags=v[12]))
        return out

    def receipt(self, bridge, *, texture_index=None):
        index = bridge.texture_index if texture_index is None else texture_index
        self.write(self.twin_offset + RECEIPTS + 128 * bridge.texture_index,
                   struct.pack("<IQQQQQIIII64x", 1, bridge.token, bridge.occurrence, bridge.encode_serial,
                               self.nonce_lo, self.nonce_hi, self.epoch, self.generation, index, 0))


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    return _build_host(tmp_path_factory.mktemp("ghost-copy-delivery"))


class TwinRunning(Running):
    """The delivery host as a SourceV4 renderer whose display surface shows a ghost."""

    def __init__(self, exe, *mode):
        self.process = subprocess.Popen(
            [str(exe), str(os.getpid()), str(gpuchannel.process_birth(os.getpid())), *mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, **quiet_spawn_kwargs())
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        assert self.receive() == "ready"


class Helper(TwinRunning):
    """The ghost copy's helper, its own process: it creates the two "-copy-"
    textures, takes key 1 (checking every pixel) and returns key 0."""

    def __init__(self, exe, wire):
        self.process = subprocess.Popen(
            [str(exe), "helper", str(wire.nonce_lo), str(wire.nonce_hi)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, **quiet_spawn_kwargs())
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        assert self.receive() == "helper ready"


def clean_return(run, client, bridge, marker):
    """The clean helper's part: take key 1, check the clean picture, give key 0."""
    assert run.command(f"take {bridge.texture_index} {marker}") == "take 1"
    client.acknowledge_metadata(bridge)
    assert run.command(f"give {bridge.texture_index}") == "give"


def twin_bridge(wire, bridge):
    """The twin bridge carrying this clean picture's decision."""
    twins = eventually(lambda: [b for b in wire.bridges() if b.encode_serial == bridge.encode_serial])
    twin = twins[0]
    assert (twin.occurrence, twin.pts, twin.duration) == (bridge.occurrence, bridge.pts, bridge.nominal_duration)
    assert twin.ready == 1 and twin.flags == 0 and twin.identity == wire.identity()
    return twin


def clean_active(run):
    fields = run.command("status").split()
    assert fields[1:3] == ["3", "0"] and fields[6] == "0", fields  # ACTIVE, no reason, never quarantined


def finish(run, client):
    assert run.command("stop") == "stopped"
    eventually(lambda: run.command("status").split()[1] == "0", timeout=4)
    assert run.command("settled") == "settled"
    client.close()
    assert run.command("exit") == "exit"
    assert run.process.wait(timeout=3) == 0


class ChannelHost:
    """The CPU-only channel producer (gpu_channel_host.cpp), one JSON reply per command."""

    def __init__(self, tmp_path, lo, hi):
        spec = importlib.util.spec_from_file_location("twin_wire_build", ROOT / "tools/build_plugin.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        vc = build.find_vcvars32()
        assert vc, "the x86 MSVC toolchain is required"
        exe = tmp_path / "gpu_channel_host.exe"
        flags = [f for f in build.COMMON_FLAGS if not f.startswith("/std:")]
        build._cl(vc, flags + ["/std:c++17", "/EHsc", "/DGC_TEST_HOST", f"/I{SOURCE}", str(SOURCE / "gpu_channel.cpp"),
                  str(SOURCE / "gpu_channel_host.cpp"), f"/Fe:{exe}", f"/Fo{tmp_path}\\", "/link", "kernel32.lib"],
                  tmp_path)
        self.process = subprocess.Popen([str(exe), str(os.getpid()), str(gpuchannel.process_birth(os.getpid())),
                                         str(lo), str(hi), "641", "481", "3"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, **quiet_spawn_kwargs())
        self.info = json.loads(self.process.stdout.readline())

    def command(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def quit(self):
        self.process.stdin.write("quit\n")
        self.process.stdin.flush()
        assert self.process.wait(timeout=3) == 0

    def close(self):
        if self.process.poll() is None:
            self.process.kill()  # Exact owned fixture process only.
        self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()


def check_twin_header(wire, info, lo, hi):
    assert info["result"] == "ok" and info["header_bytes"] == HEADER_BYTES
    assert info["twin_name_offset"] == TWIN_NAMES_AT
    assert wire.version == 3 and (wire.nonce_lo, wire.nonce_hi, wire.epoch, wire.generation) == (lo, hi, 7, 3)
    payload_offset, payload_stride = struct.unpack_from("<II", wire.raw(PAYLOAD_AT, 8))
    offer_count = wire.u32(OFFER_COUNT_AT)
    assert wire.twin_offset == info["twin_offset"] == payload_offset + payload_stride * offer_count
    assert wire.twin_offset % 64 == 0 and wire.twin_bytes == TWIN_BYTES == info["twin_bytes"]
    assert wire.total_bytes == info["total_bytes"] == wire.twin_offset + TWIN_BYTES
    assert wire.names(TWIN_NAMES_AT) == ("Local\\SM64.GpuChannel.fixture.copy.a",
                                        "Local\\SM64.GpuChannel.fixture.copy.b")
    assert wire.names(320) == ("Local\\SM64.GpuChannel.fixture.a", "Local\\SM64.GpuChannel.fixture.b")
    assert not any(wire.raw(RESERVED_AT, HEADER_BYTES - RESERVED_AT))


def check_twin_client_and_status(wire, host):
    # The twin starts OFF with this channel's identity.
    status = wire.status()
    assert (status.seq, status.state, status.reason, status.lane) == (2, OFF, 0, 0)
    assert status.identity == wire.identity()
    reply = host.command("twinclient")
    assert (reply["result"], reply["kind"]) == ("ok", CLIENT_OFF)
    wire.request(5)
    reply = host.command("twinclient")
    assert (reply["result"], reply["kind"], reply["token"]) == ("ok", CLIENT_READY, 5)
    assert host.command("twinstatus 2 0 5 900 4 1")["result"] == "ok"
    status = wire.status()
    assert (status.state, status.reason, status.lane, status.resolved_pts, status.copied, status.missed) == (
        ACTIVE, 0, 5, 900, 4, 1)


def check_twin_bridge_custody(wire, host, lo, hi):
    """A clean offer and its SELECTED decision, then the twin bridge with that
    decision's identity, its receipt and its retirement."""
    assert host.command("publish 0 1 10 0")["result"] == "ok"
    body = struct.pack("<IQQQQIIQQQQQII32x", 1, 1, 10, lo, hi, 7, 3, 1, 0, 3000, 0, 0, 0, 0)
    wire.write(wire.u32(DECISION_AT), body)  # slot 0: SELECTED, encode serial 1, pts 0
    assert host.command("take")["result"] == "ok"
    assert host.command("twinbridge 1 1 0")["result"] == "ok"
    (bridge,) = wire.bridges()
    assert (bridge.token, bridge.occurrence, bridge.encode_serial, bridge.pts, bridge.duration) == (1, 10, 1, 0, 3000)
    assert (bridge.texture_index, bridge.flags, bridge.identity) == (1, 0, wire.identity())
    assert host.command("twinbridge 1 2 0")["result"] == "busy"
    wire.receipt(bridge, texture_index=0)
    assert host.command("twinreceipt")["result"] == "invalid"
    assert host.command("twinkey0 1 1")["result"] == "ok" and not wire.bridges()
    assert host.command("twinbridge 0 2 0")["result"] == "ok"
    (bridge,) = wire.bridges()
    wire.receipt(bridge)
    reply = host.command("twinreceipt")
    assert (reply["result"], reply["slot"], reply["token"]) == ("ok", 0, 2)
    # A client request with another channel's identity is refused.
    wire.write(wire.twin_offset + CLIENT, struct.pack("<IQQQII88x", CLIENT_READY, 6, lo, hi, 8, 3))
    assert host.command("twinclient")["result"] == "invalid"


def test_twin_wire_layout_cpu(tmp_path):
    """The header's twin fields and the 768-byte twin block, decoded by offset
    from a real producer: initial OFF status, client request, status, bridge,
    receipt and retirement."""
    lo, hi = secrets.randbits(63) | 1, secrets.randbits(63)
    host, wire = ChannelHost(tmp_path, lo, hi), None
    try:
        wire = TwinWire(lo, hi)
        check_twin_header(wire, host.info, lo, hi)
        check_twin_client_and_status(wire, host)
        check_twin_bridge_custody(wire, host, lo, hi)
        host.quit()
    finally:
        if wire:
            wire.close()
        host.close()


def lane_off_asks_nothing(run, client, wire):
    names = wire.names(TWIN_NAMES_AT)
    assert names == tuple(f"Local\\SM64GpuImage-{wire.nonce_hi:016x}{wire.nonce_lo:016x}-{wire.epoch}-copy-{i}"
                          for i in range(2))
    assert not set(names) & set(client.texture_names)
    status = wire.status()
    assert (status.state, status.lane, status.reason) == (OFF, 0, 0) and status.identity == wire.identity()
    bridge = select(client, offer_for(run, client, 5), 1)
    clean_return(run, client, bridge, 5)
    assert run.command("displaystats") == "display 0 0 0"


def copy_one(run, client, wire, helper, marker, serial, twin_marker):
    """One picture through both lanes; returns the twin bridge, given back."""
    bridge = select(client, offer_for(run, client, marker), serial)
    twin = twin_bridge(wire, bridge)
    clean_return(run, client, bridge, marker)  # the clean picture: never a ghost
    assert helper.command(f"take {twin.texture_index} {twin_marker}") == "take 1"
    wire.receipt(twin)
    assert helper.command(f"give {twin.texture_index}") == "give"
    eventually(lambda: not wire.bridges())
    return bridge


def held_twins_miss_only_the_twin(run, client, wire, helper):
    """Both twin textures held by the helper: the next twin picture is missed,
    its clean picture is not."""
    held = []
    for marker, serial in ((9, 4), (10, 5)):
        bridge = select(client, offer_for(run, client, marker), serial)
        held.append(twin_bridge(wire, bridge))
        clean_return(run, client, bridge, marker)
        assert helper.command(f"take {held[-1].texture_index} {marker + 100}") == "take 1"
        wire.receipt(held[-1])
    bridge = select(client, offer_for(run, client, 11), 6)
    status = eventually(lambda: (s := wire.status()).missed == 1 and s)
    assert (status.copied, status.resolved_pts, status.state) == (4, bridge.pts, ACTIVE)
    clean_return(run, client, bridge, 11)
    clean_active(run)
    return held


def off_stops_only_the_twin(run, client, wire, helper, held):
    """OFF: no new twin copies; the held keys come back; STOPPED, no reason."""
    wire.request(0)
    for twin in held:
        assert helper.command(f"give {twin.texture_index}") == "give"
    status = eventually(lambda: (s := wire.status()).state == STOPPED and s, timeout=4)
    assert (status.reason, status.lane, status.copied, status.missed) == (0, 1, 4, 1)
    assert run.command("displaystats").split()[2] == "0"  # the renderer stops copying
    calls = int(run.command("displaystats").split()[1])
    bridge = select(client, offer_for(run, client, 12), 7)
    clean_return(run, client, bridge, 12)
    assert int(run.command("displaystats").split()[1]) == calls  # off again: never asked
    assert not wire.bridges()
    clean_active(run)


def test_ghost_copy_records_the_display_while_clean_stays_clean(host, modern_gl):
    """Clean == clean with the lane on; the twin gets the displayed picture
    (or the clean one when no ghost was drawn); no free twin credit is a
    missed twin picture and never touches the clean one; OFF stops the twin
    while the clean capture keeps recording; nothing asks for the display
    while the lane is off."""
    run, client, helper, wire = TwinRunning(host, "twin"), None, None, None
    try:
        client = run.start(901)
        wire = TwinWire(client.header.nonce_lo, client.header.nonce_hi)
        lane_off_asks_nothing(run, client, wire)
        helper = Helper(host, wire)
        wire.request(1)
        status = eventually(lambda: (s := wire.status()).state == ACTIVE and s)
        assert (status.lane, status.reason, status.copied, status.missed) == (1, 0, 0, 0)
        assert run.command("displaystats").split()[2:] == ["1", "1"]  # the renderer's consumer is on
        bridge = copy_one(run, client, wire, helper, 7, 2, 107)  # a ghost drawn: the twin has it
        status = wire.status()
        assert (status.copied, status.missed, status.resolved_pts) == (1, 0, bridge.pts)
        assert run.command("mode same") == "mode"
        copy_one(run, client, wire, helper, 8, 3, 8)  # no ghost drawn: the twin takes the clean picture
        assert run.command("mode ghost") == "mode"
        held = held_twins_miss_only_the_twin(run, client, wire, helper)
        off_stops_only_the_twin(run, client, wire, helper, held)
        assert helper.command("exit") == "exit"
        assert helper.process.wait(timeout=3) == 0
        helper = None
        finish(run, client)
        client = None
    finally:
        if wire:
            wire.close()
        if helper:
            helper.close()
        if client:
            client.close()
        run.close()


def test_a_twin_fault_stops_only_the_twin(host, modern_gl):
    run, client, helper, wire = TwinRunning(host, "twin"), None, None, None
    try:
        client = run.start(902)
        wire = TwinWire(client.header.nonce_lo, client.header.nonce_hi)
        helper = Helper(host, wire)
        wire.request(1)
        eventually(lambda: wire.status().state == ACTIVE)
        assert run.command("twinfault") == "twinfault"
        bridge = select(client, offer_for(run, client, 7), 1)  # the clean picture is unaffected
        status = eventually(lambda: (s := wire.status()).state == STOPPED and s, timeout=4)
        assert (status.reason, status.copied) == (GD_TWIN_BRIDGE_FAILED, 0)
        clean_return(run, client, bridge, 7)
        bridge = select(client, offer_for(run, client, 8), 2)
        clean_return(run, client, bridge, 8)
        clean_active(run)
        # A faulted twin bridge is never trusted again in this capture run.
        wire.request(2)
        status = eventually(lambda: (s := wire.status()).lane == 2 and s)
        assert (status.state, status.reason) == (STOPPED, GD_TWIN_BRIDGE_FAILED)
        clean_active(run)
        assert helper.command("exit") == "exit"
        helper = None
        finish(run, client)
        client = None
    finally:
        if wire:
            wire.close()
        if helper:
            helper.close()
        if client:
            client.close()
        run.close()


def test_killing_the_twin_helper_mid_run_leaves_clean_recording(host, modern_gl):
    run, client, helper, wire = TwinRunning(host, "twin"), None, None, None
    try:
        client = run.start(903)
        wire = TwinWire(client.header.nonce_lo, client.header.nonce_hi)
        helper = Helper(host, wire)
        wire.request(1)
        eventually(lambda: wire.status().state == ACTIVE)
        bridge = select(client, offer_for(run, client, 7), 1)
        twin = twin_bridge(wire, bridge)
        clean_return(run, client, bridge, 7)
        assert helper.command(f"take {twin.texture_index} 107") == "take 1"
        wire.receipt(twin)
        helper.process.kill()  # Exact owned helper process, holding the twin key.
        helper.process.wait(timeout=3)
        wire.request(0)  # its client sees the helper gone
        started = time.monotonic()
        for serial, marker in enumerate(range(8, 14), start=2):
            bridge = select(client, offer_for(run, client, marker), serial)
            clean_return(run, client, bridge, marker)
            clean_active(run)
        status = eventually(lambda: (s := wire.status()).state == STOPPED and s, timeout=5)
        print(f"twin helper killed: STOPPED reason={status.reason} after {time.monotonic() - started:.2f}s")
        # Measured on NVIDIA: a dead process's keyed mutex is abandoned, so key 0
        # comes straight back and the stop is clean (reason 0, ~0.2 s). A driver
        # that kept it held would end in GD_TWIN_KEY_ABANDONED instead.
        assert status.reason in (0, GD_TWIN_KEY_ABANDONED), status
        bridge = select(client, offer_for(run, client, 20), 8)
        clean_return(run, client, bridge, 20)
        clean_active(run)
        helper.close()
        helper = None
        finish(run, client)  # the epoch's GL retirement deletes the twin's names
        client = None
    finally:
        if wire:
            wire.close()
        if helper:
            helper.close()
        if client:
            client.close()
        run.close()


def test_a_twin_key_held_past_the_deadline_is_abandoned_not_waited_for(host, modern_gl):
    """A live helper that never returns its key: the stop gives up after the
    picture deadline (max_age_ms, 2 s in this host) with GD_TWIN_KEY_ABANDONED,
    the clean capture never waits, and the epoch's end deletes the twin's names
    while the helper still holds the key."""
    run, client, helper, wire = TwinRunning(host, "twin"), None, None, None
    try:
        client = run.start(905)
        wire = TwinWire(client.header.nonce_lo, client.header.nonce_hi)
        helper = Helper(host, wire)
        wire.request(1)
        eventually(lambda: wire.status().state == ACTIVE)
        bridge = select(client, offer_for(run, client, 7), 1)
        twin = twin_bridge(wire, bridge)
        clean_return(run, client, bridge, 7)
        assert helper.command(f"take {twin.texture_index} 107") == "take 1"
        wire.request(0)
        started = time.monotonic()
        serial = 2
        while wire.status().state != STOPPED:
            assert time.monotonic() - started < 4, "the abandoned twin key held the stop"
            bridge = select(client, offer_for(run, client, 8 + serial), serial)
            clean_return(run, client, bridge, 8 + serial)
            serial += 1
        elapsed = time.monotonic() - started
        status = wire.status()
        print(f"held twin key: STOPPED reason={status.reason} after {elapsed:.2f}s, {serial - 2} clean pictures meanwhile")
        assert status.reason == GD_TWIN_KEY_ABANDONED and 1.9 < elapsed < 3.5
        clean_active(run)
        # Its names wait for the epoch's GL retirement: no other lane this run.
        wire.request(2)
        status = eventually(lambda: (s := wire.status()).lane == 2 and s)
        assert (status.state, status.reason) == (STOPPED, GD_TWIN_KEY_ABANDONED)
        finish(run, client)
        client = None
        assert helper.command(f"give {twin.texture_index}") == "give"
        assert helper.command("exit") == "exit"
        helper = None
    finally:
        if wire:
            wire.close()
        if helper:
            helper.close()
        if client:
            client.close()
        run.close()


@pytest.mark.skipif(os.name != "nt" or os.environ.get("SM64_TEST_GPU_BRIDGE") != "1",
                    reason="explicit Windows NVIDIA witness: set SM64_TEST_GPU_BRIDGE=1")
def test_twin_lane_at_cadence_refuses_no_clean_picture_and_reports_renderer_cost(cadence_host, tmp_path, monkeypatch):
    """The cadence host (60 VIs, 30 pictures, 1600x1200, real NVENC helper and
    media path) as a SourceV4 renderer drawing a ghost on every picture: the
    lane off is the baseline, then on with this host returning every twin
    texture at once. The clean recording stays exact with zero refusals, and
    the renderer's time per VI is reported for both."""
    monkeypatch.setenv("SM64_CADENCE_TWIN", "1")
    measured = {}
    for lane in ("off", "on"):
        out = tmp_path / lane
        out.mkdir()
        pipeline = Pipeline(cadence_host, out, isolated=True, delay_s=0, dimensions=(1600, 1200),
                            frames=330, vi_per_picture=2, presentation=True)
        twin = None
        try:
            pipeline.open()
            if lane == "on":
                assert pipeline.native.command("twin on") == "twin requested"
            pipeline.start()
            pipeline.pump()
            pipeline.result()  # zero refusals, every clean picture decoded exactly
            if lane == "on":
                twin = pipeline.native.command("twin off")
        finally:
            pipeline.close()
        fields = dict(f.split("=", 1) for f in pipeline.evidence["stream"].split() if "=" in f)
        measured[lane] = (fields, twin)
    for lane, (fields, twin) in measured.items():
        print(f"lane {lane}: VIs={fields['timed']} renderer us/VI median,p99,max={fields['vi_us']} "
              f"capture={fields['capture_us']} twin(state,copied,missed)={fields['twin']} {twin or ''}")
    state, copied, missed = map(int, measured["on"][0]["twin"].split(","))
    assert state == ACTIVE and copied >= 320 and missed <= 10, measured["on"]
    assert measured["off"][0]["twin"] == "0,0,0"
    assert "state=3 reason=0" in measured["on"][1], measured["on"][1]
    # A generous ceiling, not a target: the lane must not cost a VI's budget.
    off_p99 = float(measured["off"][0]["vi_us"].split(",")[1])
    on_p99 = float(measured["on"][0]["vi_us"].split(",")[1])
    assert on_p99 < off_p99 + 4000, (off_p99, on_p99)


def test_a_renderer_without_sourcev4_refuses_the_lane(host, modern_gl):
    """'Capture layer predates ghost copies': the lane stops at once, the clean
    capture records on."""
    run, client, wire = TwinRunning(host), None, None
    try:
        client = run.start(904)
        wire = TwinWire(client.header.nonce_lo, client.header.nonce_hi)
        wire.request(1)
        status = eventually(lambda: (s := wire.status()).state == STOPPED and s)
        assert (status.reason, status.lane) == (GD_TWIN_UNAVAILABLE, 1)
        bridge = select(client, offer_for(run, client, 7), 1)
        clean_return(run, client, bridge, 7)
        clean_active(run)
        finish(run, client)
        client = None
    finally:
        if wire:
            wire.close()
        if client:
            client.close()
        run.close()

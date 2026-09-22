"""Real x86 producer / x64 Python metadata channel, CPU-only and hidden."""

from __future__ import annotations
import ctypes as C
import importlib.util
import json
import mmap
import os
from pathlib import Path
import queue
import secrets
import struct
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = next(
    p
    for p in Path(__file__).resolve().parents
    if (p / "tools/build_plugin.py").is_file()
)
SOURCE = Path(__file__).resolve().parents[1] / "plugin/gfxwrap"
from sm64_events.replay import gpuchannel as G

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows x86/x64 IPC witness")


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    spec = importlib.util.spec_from_file_location(
        "channel_build", ROOT / "tools/build_plugin.py"
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()   # vswhere first: any edition, any year
    assert vc, "bounded native tests require the existing x86 MSVC toolchain"

    def run(*args, **kwargs):
        kwargs.update(quiet_spawn_kwargs())
        return subprocess.run(*args, **kwargs)

    build.subprocess = SimpleNamespace(run=run)
    out = tmp_path_factory.mktemp("gpu-channel-host")
    exe = out / "gpu_channel_host.exe"
    flags = [f for f in build.COMMON_FLAGS if not f.startswith("/std:")]
    build._cl(
        vc,
        flags
        + [
            "/std:c++17",
            "/EHsc",
            "/DGC_TEST_HOST",
            f"/I{ROOT / 'plugin/gfxwrap'}",
            str(SOURCE / "gpu_channel.cpp"),
            str(SOURCE / "gpu_channel_host.cpp"),
            f"/Fe:{exe}",
            f"/Fo{out}\\",
            "/link",
            "kernel32.lib",
        ],
        out,
    )
    return exe


class Host:
    def __init__(
        self,
        exe,
        width=640,
        height=480,
        *,
        nonce=None,
        generation=1,
        owner_pid=None,
        birth=None,
    ):
        self.nonce = nonce or (secrets.randbits(64), secrets.randbits(64))
        self.generation = generation
        owner_pid = owner_pid or os.getpid()
        birth = G.process_birth(owner_pid) if birth is None else birth
        self.process = subprocess.Popen(
            [
                str(exe),
                str(owner_pid),
                str(birth),
                *(str(v) for v in self.nonce),
                str(width),
                str(height),
                str(generation),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **quiet_spawn_kwargs(),
        )
        self.lines = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.info = self.receive()

    def _read(self):
        for line in self.process.stdout:
            self.lines.put(line)

    def receive(self):
        try:
            return json.loads(self.lines.get(timeout=5))
        except queue.Empty as exc:
            raise AssertionError(
                "owned native host did not reply within five seconds"
            ) from exc

    def ask(self, command):
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()
        return self.receive()

    def client(self, **overrides):
        args = dict(
            nonce=self.nonce,
            epoch=7,
            generation=self.generation,
            producer_pid=self.info["pid"],
            producer_birth=self.info["birth"],
        )
        args.update(overrides)
        return G.Client(**args)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.write("quit\n")
            self.process.stdin.flush()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()  # only this test's owned fixture child
                self.process.wait(timeout=3)
        self.reader.join(timeout=2)
        assert not self.reader.is_alive()
        err = self.process.stderr.read()
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.stderr.close()
        assert self.process.returncode == 0, err

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def ok(host, command):
    result = host.ask(command)
    assert result["result"] == "ok", (command, result)
    return result


def poke(client, offset, value):
    G.U32.from_address(client._view + offset).value = value


def snapshot(client, offset, kind):
    return kind.from_buffer_copy(C.string_at(client._view + offset, C.sizeof(kind)))


@pytest.mark.parametrize(
    "width,height,sample_bytes,map_bytes",
    [(640, 480, 19200, 176128), (641, 481, 19764, 180736)],
)
def test_real_x86_offer_decision_bridge_and_capacity(
    native, width, height, sample_bytes, map_bytes
):
    assert C.sizeof(C.c_void_p) == 8
    with Host(native, width, height) as host, host.client() as client:
        assert host.info["result"] == "ok"
        assert host.info["pointer_bytes"] == 4
        assert host.info["table_offset"] == G.Header.table.offset == 192
        assert host.info["name_offset"] == G.Header.texture_names.offset == 320
        assert (
            host.info["sample_bytes"] == sample_bytes
            and host.info["total_bytes"] == map_bytes
        )
        assert client.header.luid_low == 0xABCDEF12 and client.header.luid_high == -7
        assert client.texture_names == (
            "Local\\SM64.GpuChannel.fixture.a",
            "Local\\SM64.GpuChannel.fixture.b",
        )
        ok(host, "status 2")
        # Fixed original occurrence order despite slot order and repeated/reset RAM counter.
        slots = [6, 0, 3, 2, 7, 4, 1, 5]
        counters = [900, 900, 1, 2, 3, 4, 5, 6]
        for occurrence, (slot, counter) in enumerate(
            zip(slots, counters, strict=True), 1
        ):
            ok(host, f"publish {slot} {occurrence} {occurrence} {counter}")
        assert host.ask("publish 6 9 9 7")["result"] == "busy"
        offers = client.offers()
        assert [o.occurrence for o in offers] == list(range(1, 9))
        assert client.offers() == []
        for o, counter in zip(offers, counters, strict=True):
            assert o.width == width and o.height == height
            assert (
                o.list_qpc == 10000 + o.occurrence and o.boundary_qpc == o.list_qpc + 5
            )
            assert o.lengths == (4, 8) + (0,) * 14
            assert o.stamp_bytes == struct.pack("<I", counter) + bytes(
                range(o.occurrence + 4, o.occurrence + 12)
            )
            assert o.sample == bytes(
                255 if i % 4 == 3 else (i * 17 + o.occurrence * 13) % 256
                for i in range(sample_bytes)
            )
        assert host.ask("take")["result"] == "empty"
        with pytest.raises(G.ProtocolError, match="out-of-order"):
            client.reply(
                offers[1], G.SELECTED, encode_serial=1, pts=0, nominal_duration=90
            )
        client.reply(
            offers[0],
            G.SELECTED,
            encode_serial=1,
            pts=0,
            nominal_duration=90,
            force_idr=True,
        )
        d = ok(host, "take")
        assert (
            d["slot"],
            d["occurrence"],
            d["encode_serial"],
            d["pts"],
            d["duration"],
        ) == (6, 1, 1, 0, 90)
        ok(host, "bridge 0 1 6")
        b = client.bridges()[0]
        assert b == G.Bridge(0, 1, 1, 1, 0, 90, True)
        client.acknowledge_metadata(b)
        ok(host, "receipt")
        # Receiving a packet/metadata acknowledgement cannot return key0 or free a bridge.
        assert host.ask("bridge 0 2 6")["result"] == "busy"
        assert snapshot(client, client.header.bridge_offset, G.BridgeWire).ready == 1
        assert host.ask("key0 0 99")["result"] == "stale"
        ok(host, "key0 0 1")  # explicit fixture-only independent ownership signal
        assert host.ask("bridge 0 2 6")["result"] == "repeated"
        ok(host, "retire 6 1")
        client.reply(offers[1], G.COALESCED, retained_occurrence=1, retained_serial=1)
        assert ok(host, "take")["retained"] == 1
        ok(host, "retire 0 2")
        for o in offers[2:]:
            client.reply(o, G.FAILED if o.occurrence == 3 else G.SUPPRESSED, reason=5)
            assert ok(host, "take")["kind"] in {G.FAILED, G.SUPPRESSED}
            ok(host, f"retire {o.slot} {o.token}")
        ok(host, "publish 6 9 9 0")
        next_offer = client.offers()[0]
        assert next_offer.occurrence == 9
        assert host.ask("retire 6 1")["result"] == "stale"
        assert host.ask("publish 0 10 8 4")["result"] == "stale"


def test_torn_invalid_stale_repeated_commands(native):
    with Host(native) as host, host.client() as client:
        ok(host, "publish 0 1 1 100")
        ok(host, "publish 1 2 2 100")
        h = client.header
        o_offset = h.offer_offset
        original_seq = client._word(o_offset)
        poke(client, o_offset, original_seq + 1)
        assert client.offers() == []
        # A torn older slot must never let a later offer be consumed.
        poke(client, o_offset, original_seq)
        assert [o.occurrence for o in client.offers()] == [1, 2]
        # Native also independently rejects a later command while head unresolved.
        d = G.Decision(
            kind=G.SELECTED,
            token=2,
            occurrence=2,
            nonce_lo=h.nonce_lo,
            nonce_hi=h.nonce_hi,
            epoch=h.epoch,
            generation=h.generation,
            encode_serial=2,
            pts=1,
            nominal_duration=90,
        )
        client._write(h.decision_offset + 128, d)
        assert host.ask("take")["result"] == "out_of_order"
        # Restore fixture-only command half, then examine every invalid form at head.
        C.memset(client._view + h.decision_offset + 128, 0, 128)
        d.token = 1
        d.occurrence = 1
        d.encode_serial = 1
        d.pts = 0
        for field, value, expected in [
            ("epoch", 999, "invalid"),
            ("token", 888, "stale"),
            ("nominal_duration", 0, "invalid"),
            ("flags", 2, "invalid"),
        ]:
            old = getattr(d, field)
            setattr(d, field, value)
            client._write(h.decision_offset, d)
            assert host.ask("take")["result"] == expected
            setattr(d, field, old)
        client._write(h.decision_offset, d)
        seq = client._word(h.decision_offset)
        poke(client, h.decision_offset, seq + 1)
        assert host.ask("take")["result"] == "busy"
        poke(client, h.decision_offset, seq)
        ok(host, "take")
        client._write(h.decision_offset, d)
        assert host.ask("take")["result"] == "repeated"
        ok(host, "bridge 0 1 0")
        ok(host, "retire 0 1")
        ok(host, "publish 0 3 3 1")
        assert host.ask("take")["result"] == "stale"


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "reserved",
        "offer_count",
        "bridge_count",
        "dimensions",
        "table_overflow",
        "table_sum",
        "offset_overflow",
        "sample_bytes",
        "sample_capacity",
        "ram_budget",
        "packet_count",
        "packet_budget",
        "names",
        "unterminated",
        "utf16",
        "backing_length",
    ],
)
def test_header_independent_native_and_python_refusals(native, mutation):
    with Host(native) as host, host.client() as client:
        original = bytes(client.header)
        h = G.Header.from_buffer_copy(original)
        scalar = {
            "version": ("version", h.version + 1),
            "offer_count": ("offer_count", 9),
            "bridge_count": ("bridge_count", 3),
            "dimensions": ("width", 0xFFFFFFFF),
            "offset_overflow": ("payload_offset", 0xFFFFFFC0),
            "sample_bytes": ("sample_bytes", h.sample_bytes + 4),
            "sample_capacity": ("sample_capacity", h.sample_capacity + 4),
            "ram_budget": ("ram_budget", h.total_bytes - 1),
            "packet_count": ("packet_count", 9),
            "packet_budget": ("packet_bytes", G.MAX_PACKET + 1),
            "backing_length": ("total_bytes", h.total_bytes + 64),
        }
        if mutation in scalar:
            setattr(h, *scalar[mutation])
        elif mutation == "reserved":
            h.reserved[500] = 1
        elif mutation == "table_overflow":
            h.table[0].offset = 0xFFFFFFFF
        elif mutation == "table_sum":
            h.table[0].length = 2048
        elif mutation == "names":
            h.texture_names[1] = h.texture_names[0]
        elif mutation == "unterminated":
            h.texture_names[0] = (G.U16 * 128)(*([65] * 128))
        elif mutation == "utf16":
            h.texture_names[0][0] = 0xD800
        with pytest.raises(G.ProtocolError):
            G.validate_header(h, client.header.total_bytes)
        C.memmove(client._view, bytes(h), 2048)
        assert host.ask("validate")["result"] == "invalid"
        C.memmove(client._view, original, 2048)
        ok(host, "validate")


def test_header_budget_refused_before_mapping_create(native):
    with Host(native, 8193, 8193) as host:
        assert host.info["result"] == "invalid" and host.info["total_bytes"] == 0
        with pytest.raises(FileNotFoundError):
            host.client()


def test_owner_generation_close_and_thread_affinity(native):
    with Host(native) as host:
        with pytest.raises(G.ProtocolError, match="stale session"):
            host.client(generation=2)
        with pytest.raises(G.ProtocolError, match="stale session"):
            host.client(producer_birth=host.info["birth"] + 1)
        client = host.client()
        with pytest.raises(G.ProtocolError, match="already exists"):
            host.client()
        assert host.ask("thread")["result"] == "wrong_worker"
        failures = []

        def wrong_thread():
            try:
                client.status()
            except RuntimeError as exc:
                failures.append(str(exc))

        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join(timeout=2)
        assert failures and "serialized media worker" in failures[0]
        client.close()
        assert host.ask("status 2")["result"] == "closed"
        with pytest.raises(G.ProtocolError, match="already used"):
            host.client()
    with Host(native, generation=2) as fresh, fresh.client() as client:
        assert client.header.generation == 2
        ok(fresh, "close")
        with pytest.raises(RuntimeError, match="stopped"):
            client.status()


@pytest.mark.parametrize("command,state,reason", [
    ("close", 3, 0),
    ("sourcegap", 3, 0x20000000 | 10010),
    ("cancel", 3, 0x20000000 | 10000),
])
def test_terminal_reason_survives_native_disposal(native, command, state, reason):
    with Host(native) as host, host.client() as client:
        ok(host, "status 2")
        ok(host, command)
        # The producer has actually unmapped/closed its handles. The independent
        # x64 reader retains the mapping and sees the final x86 wire bytes.
        terminal = snapshot(client, client.header.status_offset, G.Status)
        assert terminal.seq > 0 and not terminal.seq % 2
        assert (terminal.state, terminal.reason) == (state, reason)
        assert (terminal.nonce_lo, terminal.nonce_hi) == host.nonce
        assert (terminal.epoch, terminal.generation) == (7, host.generation)
        with pytest.raises(RuntimeError, match=f"state={state} reason={reason}"):
            client.status()
        before = bytes(terminal)
        ok(host, "close")
        ok(host, "cancel")
        assert bytes(snapshot(client, client.header.status_offset, G.Status)) == before


@pytest.mark.parametrize("first_close", ["close", "cancel", f"close {0x20000000 | 10012}"])
def test_explicit_close_cannot_erase_an_existing_fault(native, first_close):
    with Host(native) as host, host.client() as client:
        ok(host, "fault")  # Publishes the real GD_SOURCE_GAP constant.
        initial = snapshot(client, client.header.status_offset, G.Status)
        assert (initial.state, initial.reason) == (4, 0x20000000 | 10010)
        assert initial.heartbeat_qpc == 12345
        # Writable IPC is not the diagnostic authority: even an externally
        # erased status cannot make disposal lose the native owner's cause.
        poke(client, client.header.status_offset + G.Status.state.offset, 3)
        poke(client, client.header.status_offset + G.Status.reason.offset, 0)
        ok(host, first_close)
        terminal = snapshot(client, client.header.status_offset, G.Status)
        assert terminal.seq > initial.seq and not terminal.seq % 2
        assert (terminal.state, terminal.reason) == (initial.state, initial.reason)
        assert terminal.heartbeat_qpc == initial.heartbeat_qpc
        assert terminal.frontier_qpc == initial.frontier_qpc
        with pytest.raises(RuntimeError, match="state=4 reason=536880922"):
            client.status()
        before = bytes(terminal)
        ok(host, "close")
        assert bytes(snapshot(client, client.header.status_offset, G.Status)) == before


def test_close_supplies_first_nonzero_reason_without_changing_fault_state(native):
    with Host(native) as host, host.client() as client:
        ok(host, "status 4")
        ok(host, f"close {0x20000000 | 10012}")
        terminal = snapshot(client, client.header.status_offset, G.Status)
        assert (terminal.state, terminal.reason) == (4, 0x20000000 | 10012)
        ok(host, "sourcegap")
        assert bytes(snapshot(client, client.header.status_offset, G.Status)) == bytes(terminal)


def test_owner_death_and_birth_mismatch(native):
    with Host(native, birth=G.process_birth(os.getpid()) + 1) as bad:
        assert bad.info["result"] == "owner_gone"
    owner = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.readline()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        **quiet_spawn_kwargs(),
    )
    try:
        with Host(native, owner_pid=owner.pid) as host:
            lo, hi = host.nonce
            name = f"{G.NAMESPACE}{hi:016x}{lo:016x}"
            with mmap.mmap(-1, host.info["total_bytes"], tagname=name) as observer:
                header = G.Header.from_buffer_copy(observer[:C.sizeof(G.Header)])
                assert header.producer_pid == host.info["pid"]
                ok(host, "status 2")
                owner.stdin.write("quit\n")
                owner.stdin.flush()
                owner.wait(timeout=3)
                assert host.ask("status 2")["result"] == "owner_gone"
                assert host.ask("publish 0 1 1 1")["result"] == "closed"
                ok(host, "cancel")
                terminal = G.Status.from_buffer_copy(observer[header.status_offset:][:C.sizeof(G.Status)])
                assert (terminal.state, terminal.reason) == (4, 0x10000000 | 8)
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)
        owner.stdin.close()


def test_offer_offsets_and_length_torn_reads_are_bounded(native):
    with Host(native) as host, host.client() as client:
        ok(host, "publish 0 1 1 9")
        h = client.header
        original = C.string_at(client._view + h.offer_offset, 256)
        for field, value in [
            ("sample_offset", 0xFFFFFFF0),
            ("stamp_bytes", 2049),
            ("width", 639),
            ("epoch", 9),
        ]:
            offer = G.OfferWire.from_buffer_copy(original)
            setattr(offer, field, value)
            C.memmove(client._view + h.offer_offset, bytes(offer), 256)
            with pytest.raises(G.ProtocolError):
                client.offers()
        C.memmove(client._view + h.offer_offset, original, 256)
        assert client.offers()[0].occurrence == 1
        # Sequence exhaustion must refuse before overwriting the client half.
        poke(client, h.decision_offset, 0xFFFFFFFE)
        with pytest.raises(G.ProtocolError, match="exhausted"):
            client.reply(next(iter(client._pending.values())), G.FAILED, reason=1)
        poke(client, h.decision_offset, 0)


def test_publication_during_empty_slot_scan_cannot_pass_older_offer(
    native, monkeypatch
):
    with Host(native) as host, host.client() as client:
        real = C.string_at
        injected = False

        def interleave(address, size):
            nonlocal injected
            value = real(address, size)
            if (
                not injected
                and address == client._view + client.header.offer_offset
                and size == 256
            ):
                injected = True
                # Reader has copied slot0 EMPTY. Producer now publishes an older
                # slot0 and a newer slot1 before reader reaches slot1.
                ok(host, "publish 0 1 40 999")
                ok(host, "publish 1 2 71 0")
            return value

        monkeypatch.setattr(C, "string_at", interleave)
        offers = client.offers()
        assert injected and [(o.token, o.occurrence) for o in offers] == [
            (1, 40),
            (2, 71),
        ]
        assert client.offers() == []


def test_torn_payload_restarts_whole_batch_and_preserves_source_identity(
    native, monkeypatch
):
    with Host(native) as host, host.client() as client:
        ok(host, "publish 0 1 1 9")
        real = C.string_at
        injected = False

        def interleave(address, size):
            nonlocal injected
            value = real(address, size)
            if (
                not injected
                and address
                == client._view + client.header.payload_offset + G.STAMP_BYTES
            ):
                injected = True
                ok(host, "publish 1 2 2 9")
            return value

        monkeypatch.setattr(C, "string_at", interleave)
        offers = client.offers()
        assert injected and [o.token for o in offers] == [1, 2]
        for o in offers:
            assert o.sample[:8] == bytes(
                [
                    13 * o.occurrence,
                    17 + 13 * o.occurrence,
                    34 + 13 * o.occurrence,
                    255,
                    68 + 13 * o.occurrence,
                    85 + 13 * o.occurrence,
                    102 + 13 * o.occurrence,
                    255,
                ]
            )


def test_frontier_waits_for_offer_disposition_even_when_status_arrives_first(native):
    with Host(native) as host, host.client() as client:
        ok(host, "publish 0 1 40 999")
        ok(host, "publish 1 2 71 0")
        ok(host, "frontier 30000 20000 105 2")
        # Frontier occurrence105 may include explicit source omissions; exact
        # contiguous offer token2 is the CPU publication obligation.
        assert client.frontier() is None
        offers = client.offers()
        client.reply(offers[0], G.SELECTED, encode_serial=1, pts=0, nominal_duration=90)
        ok(host, "take")
        assert client.frontier() is None
        client.reply(offers[1], G.COALESCED, retained_occurrence=40, retained_serial=1)
        ok(host, "take")
        assert client.frontier() == (20000, 105, 2)
        assert host.ask("frontier 30000 20000 105 3")["result"] == "invalid"


def test_no_sequence_wrap_and_close_retains_handles_on_publication_failure(native):
    with Host(native) as host:
        client = host.client()
        publication = client.header.publication_offset
        seq = client._word(publication)
        poke(client, publication, 0xFFFFFFFE)
        assert host.ask("publish 0 1 1 9")["result"] == "exhausted"
        assert client.offers() == []
        poke(client, publication, seq)
        ok(host, "publish 0 1 1 9")
        client_seq = client._word(client.header.client_offset)
        poke(client, client.header.client_offset, 3)
        with pytest.raises(G.ProtocolError, match="interrupted"):
            client.close()
        # Retained for the owner's retry (test_gpu_owner_recovery.py): the
        # mapping, mutex and handles are still ours, the host still sees us.
        assert client._view is not None and client._map is not None and client._mutex is not None
        assert host.ask("take")["result"] == "busy"
        poke(client, client.header.client_offset, client_seq)
        client.close()
        assert client._view is None and client._map is None and client._mutex is None


def test_mapping_collision_never_reinitializes_existing_session(native):
    with Host(native) as first, first.client() as client:
        ok(first, "publish 0 1 1 9")
        before = bytes(client.header)
        with Host(native, nonce=first.nonce, generation=2) as second:
            assert second.info["result"] == "busy"
        assert bytes(client.header) == before and client.offers()[0].token == 1
        ok(first, "close")
        with Host(native, nonce=first.nonce, generation=2) as third:
            assert third.info["result"] == "busy"
        with pytest.raises(RuntimeError, match="stopped"):
            client.status()


def test_bridge_publication_race_and_order_refusal(native, monkeypatch):
    with Host(native) as host, host.client() as client:
        ok(host, "publish 0 1 40 999")
        ok(host, "publish 1 2 71 0")
        offers = client.offers()
        for serial, offer in enumerate(offers, 1):
            client.reply(
                offer,
                G.SELECTED,
                encode_serial=serial,
                pts=serial - 1,
                nominal_duration=90,
            )
            ok(host, "take")
        assert host.ask("bridge 1 1 1")["result"] == "out_of_order"
        assert host.ask("retire 0 1")["result"] == "busy"
        real = C.string_at
        injected = False

        def interleave(address, size):
            nonlocal injected
            value = real(address, size)
            if (
                not injected
                and address == client._view + client.header.bridge_offset
                and size == 128
            ):
                injected = True
                ok(host, "bridge 0 1 0")
                ok(host, "bridge 1 2 1")
            return value

        monkeypatch.setattr(C, "string_at", interleave)
        bridges = client.bridges()
        assert injected and [
            (b.token, b.occurrence, b.encode_serial) for b in bridges
        ] == [(1, 40, 1), (2, 71, 2)]
        assert client.bridges() == []
        for b in bridges:
            client.acknowledge_metadata(b)
            ok(host, "receipt")
        assert host.ask("bridge 0 3 0")["result"] == "busy"


def test_fresh_request_can_adopt_native_epoch_without_weakening_identity(native):
    with Host(native) as host:
        with pytest.raises(G.ProtocolError, match="stale session"):
            host.client(epoch=None, generation=host.generation + 1)
        with pytest.raises(G.ProtocolError, match="stale session"):
            host.client(epoch=999)
        with host.client(epoch=None) as client:
            assert client.header.epoch == 7
            state = snapshot(client, client.header.client_offset, G.ClientWire)
            assert state.epoch == 7
            poke(client, 32, 123)
            with pytest.raises(G.ProtocolError, match="immutable header"):
                client.status()

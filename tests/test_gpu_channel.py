"""Real x86 producer / x64 Python metadata channel, CPU-only and hidden."""

from __future__ import annotations
import ctypes as C
import importlib.util
import json
import math
import mmap
import os
from pathlib import Path
import queue
import secrets
import struct
import subprocess
import statistics
import sys
import threading
import time
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


def test_channel_version_matches_the_native_wire():
    """The wrapper (gpu_channel.h) and this trainer name one channel version."""
    text = (SOURCE / "gpu_channel.h").read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if row.startswith("#define GC_CHANNEL_VERSION "))
    assert int(line.split()[2].rstrip("u")) == G.VERSION


def test_a_capture_layer_from_another_build_is_named_not_garbled():
    """A wrapper and trainer from different builds fail readably, before any
    other header field is trusted (version 1 made its own bridge textures)."""
    h = G.Header()
    h.seq, h.version, h.header_bytes = 2, G.VERSION - 1, G.HEADER_BYTES
    C.memmove(h.magic, G.MAGIC, len(G.MAGIC))
    with pytest.raises(G.LayerVersionMismatch) as caught:
        G.validate_header(h, G.MAX_MAP)
    message = str(caught.value)
    assert f"version {G.VERSION - 1}" in message and f"version {G.VERSION}" in message
    assert "different builds" in message and "Close Project64" in message


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
    # Version 3 adds the 768-byte twin block after the last payload.
    [(640, 480, 19200, 176896), (641, 481, 19764, 181504)],
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


def test_real_x86_twin_block_follows_the_payloads(native):
    """Version 3: the native layout puts the ghost copy's 768-byte twin block
    after the last payload, and names two twin textures of its own."""
    with Host(native, 641, 481) as host, host.client() as client:
        h = client.header
        assert h.twin_offset == h.payload_offset + 8 * h.payload_stride
        assert h.twin_offset % 64 == 0 and h.twin_bytes == G.TWIN_BYTES
        assert h.total_bytes == host.info["total_bytes"] == h.twin_offset + 768
        assert len({*client.texture_names, *client.twin_texture_names}) == 4


def test_decisions_found_together_are_taken_in_offer_order(native):
    """Renderer-thread capture takes decisions once per VI, so one pass can
    find the client's replies to two offers. A later offer in a lower slot is
    not out of order: the head's decision is taken first, then the next."""
    with Host(native) as host, host.client() as client:
        ok(host, "publish 3 1 1 100")
        ok(host, "publish 0 2 2 100")
        first, second = client.offers()
        client.reply(first, G.SUPPRESSED, reason=5)
        client.reply(second, G.SUPPRESSED, reason=5)
        assert (ok(host, "take")["slot"], ok(host, "take")["slot"]) == (3, 0)


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
        "twin_names",
        "twin_clean_name",
        "twin_offset",
        "twin_bytes",
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
            "twin_offset": ("twin_offset", h.twin_offset + 64),
            "twin_bytes": ("twin_bytes", G.TWIN_BYTES - 1),
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
        elif mutation == "twin_names":
            h.twin_texture_names[1] = h.twin_texture_names[0]
        elif mutation == "twin_clean_name":
            h.twin_texture_names[0] = h.texture_names[1]
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


def encoder_over(client, *, max_age=2.0):
    """The media tick's ChannelEncoder reading this real x86 producer."""
    from sm64_events.replay.channelencoder import ChannelEncoder
    from sm64_events.replay.gpuinput import ChannelSelection
    from test_channelencoder import Controller
    from test_gpumedia import media

    q, h, control = media(), client.header, Controller()
    decoder = SimpleNamespace(
        namespace=q.namespace, header=h, timestamp=lambda qpc: qpc / h.qpc_frequency
    )
    a = ChannelEncoder(
        ChannelSelection(client, decoder, q), control, max_pending=8, max_age=max_age
    )
    assert a.start(dict(
        op="Open", adapter_luid=[h.luid_high, h.luid_low],
        names=list(client.texture_names),
        options=dict(width=h.even_width, height=h.even_height, input_format=h.format,
                     max_packet_bytes=q.packet_limit),
    ))
    control.deliver()
    a.pump(now=time.monotonic())
    a.pump(now=time.monotonic())
    return a, q


def test_torn_status_is_no_news_to_the_tick_until_the_age_deadline(native):
    # 2026-09-22/23/25: three runs of 30 min to 5.3 h ended on one torn read.
    with Host(native) as host, host.client() as client:
        ok(host, "status 2")
        offset = client.header.status_offset
        seq = client._word(offset)
        poke(client, offset, seq + 1)  # the native writer is preempted mid-copy
        with pytest.raises(BlockingIOError, match="torn producer status"):
            client.status()  # setup still refuses a torn status at once
        poke(client, offset, seq)
        a, q = encoder_over(client, max_age=0.25)
        try:
            poke(client, offset, seq + 1)
            until = time.monotonic() + 0.02
            while time.monotonic() < until:
                a.pump(now=time.monotonic())
                time.sleep(0.004)
            torn = a.status()["torn_status_reads"]
            assert torn > 0 and not a.fault and not q.closed
            poke(client, offset, seq + 2)  # the copy finishes
            a.pump(now=time.monotonic())
            a.pump(now=time.monotonic())
            assert a.status()["torn_status_reads"] == torn and not a.fault
            poke(client, offset, seq + 3)  # a writer that never finishes
            with pytest.raises(BlockingIOError, match="torn producer status for"):
                for _ in range(500):
                    a.pump(now=time.monotonic())
                    time.sleep(0.004)
            assert a.fault and q.closed
        finally:
            poke(client, offset, seq + 4)
            if not q.closed:
                q.abort("fixture complete")


def test_quiet_tick_reads_no_record_until_a_word_moves(native, monkeypatch):
    with Host(native) as host, host.client() as client:
        ok(host, "status 2")
        a, q = encoder_over(client)
        try:
            reads = []
            stable = client._stable

            def counted(offset, kind):
                reads.append(kind.__name__)
                return stable(offset, kind)

            monkeypatch.setattr(client, "_stable", counted)
            for _ in range(20):
                a.pump(now=time.monotonic())
            assert reads == [] and a.status()["pump_paths"]["quiet"] >= 20
            ok(host, "frontier 30000 20000 1 0")  # the 60 Hz status write
            a.pump(now=time.monotonic())
            assert reads == ["Status"] and a.frontier == math.nextafter(0.002, 0)
            reads.clear()
            publication = client.header.publication_offset
            poke(client, publication, client._word(publication) + 2)
            a.pump(now=time.monotonic())
            assert reads.count("Publication") == 2 and reads.count("BridgeWire") == 2
            reads.clear()
            a.pump(now=time.monotonic())
            assert reads == []
        finally:
            q.abort("fixture complete")


def test_idle_tick_costs_a_fraction_of_a_full_scan(native):
    """Microbenchmark on the real channel; live it was 0.22 ms a tick."""
    with Host(native) as host, host.client() as client:
        ok(host, "status 2")
        a, q = encoder_over(client)
        publication = client.header.publication_offset
        try:
            quiet, scan = [], []
            for _ in range(300):
                start = time.perf_counter()
                a.pump(now=time.monotonic())
                quiet.append(time.perf_counter() - start)
                poke(client, publication, client._word(publication) + 2)
                start = time.perf_counter()
                a.pump(now=time.monotonic())
                scan.append(time.perf_counter() - start)
            idle, full = statistics.median(quiet), statistics.median(scan)
            print(f"idle tick {idle * 1e6:.1f} us, full scan {full * 1e6:.1f} us")
            assert a.status()["pump_paths"]["quiet"] >= 300
            assert idle * 4 < full, (idle, full)
        finally:
            q.abort("fixture complete")


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


class TwinProducer:
    """This process as the producer of a version-3 mapping laid out by the
    I2 contract, so the twin client runs without the x86 host. Agreement with
    the native layout is the Host tests' job; this one exercises the client."""

    TWINS = ("Local\\SM64.GpuChannel.fixture.copy.a", "Local\\SM64.GpuChannel.fixture.copy.b")

    def __init__(self, width=64, height=48):
        k = self.k = G._kernel()
        k.CreateFileMappingW.restype = C.c_void_p
        k.CreateFileMappingW.argtypes = [C.c_void_p, C.c_void_p, G.U32, G.U32, G.U32,
                                         C.c_wchar_p]
        k.CreateEventW.restype = C.c_void_p
        k.CreateEventW.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_wchar_p]
        self.nonce = (secrets.randbits(64) | 1, secrets.randbits(64))
        name = f"{G.NAMESPACE}{self.nonce[1]:016x}{self.nonce[0]:016x}"
        pid = os.getpid()
        birth = G.process_birth(pid)
        h = self.h = G.Header(seq=2, version=G.VERSION, header_bytes=G.HEADER_BYTES)
        C.memmove(h.magic, G.MAGIC, len(G.MAGIC))
        h.producer_pid = h.owner_pid = pid
        h.producer_birth = h.owner_birth = birth
        h.nonce_lo, h.nonce_hi = self.nonce
        h.epoch, h.generation, h.qpc_frequency = 7, 1, 10_000_000
        h.width, h.height, h.even_width, h.even_height = width, height, width & ~1, height & ~1
        h.format, h.sample_stride, h.luid_low, h.luid_high = 1, 8, 0x1234, -7
        h.offer_count, h.bridge_count, h.table_count = 8, 2, 2
        h.stamp_capacity = G.STAMP_BYTES
        h.sample_bytes = h.sample_capacity = ((width + 7) // 8) * ((height + 7) // 8) * 4
        h.packet_count, h.packet_bytes, h.pending_bytes = 8, 1 << 20, 4 << 20
        h.ram_budget = G.MAX_MAP
        h.table[0], h.table[1] = G.Field(0x100, 4), G.Field(0x200, 8)
        base = G.HEADER_BYTES + 256 + 8 * 384
        h.status_offset, h.status_bytes = G.HEADER_BYTES, 256
        h.offer_offset, h.decision_offset = G.HEADER_BYTES + 256, G.HEADER_BYTES + 256 + 8 * 256
        h.bridge_offset, h.receipt_offset = base, base + 256
        h.client_offset, h.client_bytes = base + 512, 128
        h.publication_offset, h.publication_bytes = base + 640, 128
        h.payload_offset = base + 768
        h.payload_stride = (G.STAMP_BYTES + h.sample_bytes + 63) & ~63
        h.twin_offset = h.payload_offset + 8 * h.payload_stride
        h.twin_bytes = G.TWIN_BYTES
        h.total_bytes = h.twin_offset + G.TWIN_BYTES
        for i, text in enumerate(("Local\\SM64.GpuChannel.fixture.a",
                                  "Local\\SM64.GpuChannel.fixture.b")):
            C.memmove(h.texture_names[i], text.encode("utf-16-le"), len(text) * 2)
        for i, text in enumerate(self.TWINS):
            C.memmove(h.twin_texture_names[i], text.encode("utf-16-le"), len(text) * 2)
        G.validate_header(h, h.total_bytes)
        self.map = k.CreateFileMappingW(C.c_void_p(-1), None, 0x04, 0, h.total_bytes, name)
        assert self.map
        self.view = k.MapViewOfFile(self.map, 0xF001F, 0, 0, h.total_bytes)
        assert self.view
        self.events = [k.CreateEventW(None, False, False, name + suffix)
                       for suffix in (".client", ".producer")]
        assert all(self.events)
        C.memmove(self.view, bytes(h), G.HEADER_BYTES)
        self.write(h.status_offset, G.Status(state=G.ACTIVE, **self.identity()))

    def identity(self):
        return dict(nonce_lo=self.h.nonce_lo, nonce_hi=self.h.nonce_hi,
                    epoch=self.h.epoch, generation=self.h.generation)

    def word(self, offset):
        return G.U32.from_address(self.view + offset).value

    def write(self, offset, value):
        seq = self.word(offset)
        G.U32.from_address(self.view + offset).value = seq + 1
        C.memmove(self.view + offset + 4, bytes(value)[4:], C.sizeof(value) - 4)
        G.U32.from_address(self.view + offset).value = seq + 2

    def read(self, offset, kind):
        return kind.from_buffer_copy(C.string_at(self.view + offset, C.sizeof(kind)))

    def twin(self, part):
        return self.h.twin_offset + part

    def status(self, state, lane, resolved=0, copied=0, missed=0, reason=0):
        self.write(self.twin(G.TWIN_STATUS), G.TwinStatusWire(
            state=state, reason=reason, lane=lane, resolved_pts=resolved, copied=copied,
            missed=missed, **self.identity()))

    def bridge(self, slot, token, pts, *, serial=None, duration=3000, flags=0):
        self.write(self.twin(G.TWIN_BRIDGES) + slot * 128, G.BridgeWire(
            ready=1, token=token, occurrence=token + 100,
            encode_serial=token if serial is None else serial, pts=pts,
            nominal_duration=duration, texture_index=slot, flags=flags,
            **self.identity()))

    def client(self):
        return G.Client(nonce=self.nonce, epoch=7, generation=1,
                        producer_pid=os.getpid(), producer_birth=G.process_birth(os.getpid()))

    def close(self):
        self.k.UnmapViewOfFile(self.view)
        for handle in (*self.events, self.map):
            self.k.CloseHandle(handle)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_twin_client_requests_lanes_and_reads_status():
    with TwinProducer() as p, p.client() as client:
        assert client.twin_texture_names == p.TWINS
        assert client.twin_status() == G.TwinStatus(G.TWIN_OFF, 0, 0, 0, 0, 0)
        p.status(G.TWIN_OFF, 0)  # contract A2: the producer's initial twin status
        assert p.word(p.twin(G.TWIN_STATUS)) == 2
        assert client.twin_status() == G.TwinStatus(G.TWIN_OFF, 0, 0, 0, 0, 0)
        client.twin_request(1)
        wire = p.read(p.twin(G.TWIN_CLIENT), G.TwinClientWire)
        assert (wire.seq, wire.state, wire.lane, wire.nonce_lo, wire.nonce_hi,
                wire.epoch, wire.generation) == (2, G.TWIN_CLIENT_READY, 1, *p.nonce, 7, 1)
        with pytest.raises(G.ProtocolError, match="twin lane"):
            client.twin_request(1)  # every READY names a new, larger lane
        client.twin_release()
        wire = p.read(p.twin(G.TWIN_CLIENT), G.TwinClientWire)
        assert (wire.seq, wire.state, wire.lane) == (4, G.TWIN_CLIENT_OFF, 0)
        client.twin_request(3)
        assert p.read(p.twin(G.TWIN_CLIENT), G.TwinClientWire).lane == 3
        p.status(G.TWIN_ACTIVE, 3, resolved=90000, copied=2, missed=1)
        assert client.twin_status() == G.TwinStatus(G.TWIN_ACTIVE, 0, 3, 90000, 2, 1)
        offset = p.twin(G.TWIN_STATUS)
        seq = p.word(offset)
        poke(client, offset, seq + 1)  # preempted mid-copy: no news, not stale news
        with pytest.raises(BlockingIOError, match="torn twin status"):
            client.twin_status()
        poke(client, offset, seq)
        bad = p.read(offset, G.TwinStatusWire)
        bad.epoch = 8
        p.write(offset, bad)
        with pytest.raises(G.ProtocolError, match="stale message identity"):
            client.twin_status()
        bad.epoch, bad.reserved[3] = 7, 1
        p.write(offset, bad)
        with pytest.raises(G.ProtocolError, match="twin status fields"):
            client.twin_status()


def test_twin_bridges_come_in_token_order_and_receipts_land_in_their_slot():
    with TwinProducer() as p, p.client() as client:
        p.bridge(1, 4, 90000)
        p.bridge(0, 5, 93000)
        first = client.twin_bridges()
        assert [(b.texture_index, b.token, b.pts) for b in first] == [(1, 4, 90000),
                                                                     (0, 5, 93000)]
        assert client.twin_bridges() == []
        p.bridge(0, 6, 96000)
        with pytest.raises(G.ProtocolError, match="unacknowledged twin bridge"):
            client.twin_bridges()
        client.acknowledge_twin_metadata(first[0])
        receipt = p.read(p.twin(G.TWIN_RECEIPTS) + 128, G.Receipt)
        assert (receipt.seq, receipt.accepted, receipt.token, receipt.occurrence,
                receipt.encode_serial, receipt.texture_index) == (2, 1, 4, 104, 4, 1)
        with pytest.raises(G.ProtocolError, match="stale or repeated twin"):
            client.acknowledge_twin_metadata(first[0])
        client.acknowledge_twin_metadata(first[1])
        assert [b.token for b in client.twin_bridges()] == [6]
        client.acknowledge_twin_metadata(G.Bridge(0, 6, 106, 6, 96000, 3000, False))
        p.bridge(1, 7, 95000)  # a later token with an earlier picture
        with pytest.raises(G.ProtocolError, match="reordered twin bridge"):
            client.twin_bridges()


def test_a_bridge_published_mid_scan_cannot_pass_an_earlier_one(monkeypatch):
    """Twin bridges have no publication word: both slot words must hold still
    across the scan, or slot 1's later bridge would be taken before slot 0's."""
    with TwinProducer() as p, p.client() as client:
        real, injected = C.string_at, []
        # The client's own view, while it reads slot 1: slot 0 was already
        # read empty and stable, so only the pair of words reveals the race.
        slot1 = client._view + p.twin(G.TWIN_BRIDGES) + 128

        def interleave(address, size):
            value = real(address, size)
            if not injected and address == slot1 and size == 128:
                injected.append(True)
                p.bridge(0, 1, 90000)
                p.bridge(1, 2, 93000)
            return value

        monkeypatch.setattr(C, "string_at", interleave)
        assert injected == [] and [b.token for b in client.twin_bridges()] == [1, 2]
        assert injected == [True]


def test_twin_bridge_refusals():
    with TwinProducer() as p, p.client() as client:
        p.bridge(0, 1, 90000, flags=1)  # twin bridges never carry the IDR flag
        with pytest.raises(G.ProtocolError, match="twin bridge fields"):
            client.twin_bridges()
    with TwinProducer() as p, p.client() as client:
        offset = p.twin(G.TWIN_BRIDGES) + 128
        p.bridge(1, 1, 90000)
        poke(client, offset, p.word(offset) + 1)  # a writer that never finishes
        with pytest.raises(BlockingIOError, match="torn twin bridge"):
            client.twin_bridges()
        poke(client, offset, p.word(offset) + 1)
        assert [b.token for b in client.twin_bridges()] == [1]


def test_twin_calls_never_touch_the_clean_records():
    with TwinProducer() as p, p.client() as client:
        before = C.string_at(p.view, p.h.twin_offset)
        client.twin_request(1)
        p.bridge(0, 1, 90000)
        client.acknowledge_twin_metadata(client.twin_bridges()[0])
        client.twin_status()
        client.twin_release()
        assert C.string_at(p.view, p.h.twin_offset) == before


@pytest.mark.parametrize("mutation", ["duplicate", "clean", "offset", "bytes", "total"])
def test_header_refuses_a_twin_block_off_the_contract(mutation):
    with TwinProducer() as p:
        h = G.Header.from_buffer_copy(bytes(p.h))
    if mutation == "duplicate":
        h.twin_texture_names[1] = h.twin_texture_names[0]
    elif mutation == "clean":
        h.twin_texture_names[1] = h.texture_names[0]
    elif mutation == "offset":
        h.twin_offset += 64
        h.total_bytes += 64
    elif mutation == "bytes":
        h.twin_bytes = 512
        h.total_bytes -= 256
    else:
        h.total_bytes -= G.TWIN_BYTES  # a version-2 size with a version-3 header
    with pytest.raises(G.ProtocolError):
        G.validate_header(h, G.MAX_MAP)

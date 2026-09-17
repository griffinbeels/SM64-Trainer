from collections import deque
from dataclasses import replace
from types import SimpleNamespace as NS
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from sm64_events.replay.channelencoder import ChannelEncoder
from sm64_events.replay.gpuchannel import Bridge
from test_gpumedia import media, sample, stamp


class Channel:
    def __init__(self):
        self.offer_values = []
        self.bridge_values = []
        self.acks = []

    def offers(self):
        v = self.offer_values
        self.offer_values = []
        return v

    def bridges(self):
        v = self.bridge_values
        self.bridge_values = []
        return v

    def acknowledge_metadata(self, b):
        self.acks.append(b)


class Selection:
    def __init__(self, channel, q):
        self.client, self.media = channel, q
        self.certificate = None
        names = [[ord(x) for x in text] + [0] for text in ["tex-a", "tex-b"]]
        self.decoder = NS(
            header=NS(
                luid_high=0,
                luid_low=84637,
                even_width=16,
                even_height=16,
                format=1,
                texture_names=names,
            )
        )

    def handle(self, offer, *, now):
        occurrence, when, value = offer
        return self.media.offer(
            sample(value),
            stamp(occurrence + 100),
            occurrence=occurrence,
            capture_ts=when,
            now=now,
        )

    def advance_frontier(self):
        if self.certificate is not None:
            self.media.frontier(self.certificate)
        return self.certificate


class Controller:
    def __init__(self):
        self.nonce = "fixture-nonce"
        self.next = 1
        self.commands = deque()
        self.replies = deque()
        self.history = []
        self.fault = None
        self.retained = dict(valid=0, selected_serial=0, generation=0)
        self.refuse = False

    def enqueue(self, c):
        if self.refuse:
            return NS(reason="capacity")
        request_id = self.next
        self.next += 1
        self.commands.append((request_id, c))
        self.history.append(c)
        return request_id

    def status(self):
        return dict(
            fault=self.fault,
            next_request_id=self.next,
            pending_count=len(self.commands) + len(self.replies),
            inflight=None,
            done=False,
        )

    def stop(self, reason):
        self.fault = reason

    def take_result(self):
        return self.replies.popleft() if self.replies else None

    def deliver(self, code=0, *, keys=(), mutate=None):
        ident, c = self.commands.popleft()
        op = c["op"]
        payload = b""
        packet = None
        if code == 0 and op in ("Submit", "Repeat"):
            if op == "Submit":
                self.retained = dict(
                    valid=1,
                    selected_serial=c["serial"],
                    generation=self.retained["generation"] + 1,
                )
            payload = b"bounded-h264"
            packet = {n: c[n] for n in ["serial", "pts", "encoder_duration"]}
            packet.update(bytes=len(payload), keyframe=c["force_idr"])
        m = dict(
            result=code,
            error=None,
            worker_disposal_required=False,
            retained=self.retained.copy(),
            key_returns=list(keys),
            packet=packet,
        )
        if mutate:
            mutate(m)
        self.replies.append(NS(request_id=ident, metadata=m, payload=payload))
        return c


def adapter(**options):
    q = media()
    ch = Channel()
    selection = Selection(ch, q)
    control = Controller()
    a = ChannelEncoder(
        selection,
        control,
        max_pending=options.get("max_pending", 4),
        max_age=options.get("max_age", 2),
        defer_disposal=options.get("defer_disposal", False),
    )
    command = dict(
        op="Open",
        adapter_luid=[0, 84637],
        names=["tex-a", "tex-b"],
        options=dict(width=16, height=16, input_format=1, max_packet_bytes=8192),
    )
    assert a.start(command)
    control.deliver()
    a.pump(now=0)
    return a, ch, control, q, selection


def first(a, ch):
    ch.offer_values = [(7, 1000, 20)]
    ch.bridge_values = [Bridge(0, 17, 7, 1, 0, 3000, True)]
    a.pump(now=0.01)
    return ch.bridge_values


def receipt(token=17, serial=1, slot=0):
    return dict(slot=slot, bridge_token=token, serial=serial)


def test_session_owned_abort_preserves_helper_keys_until_outer_retirement():
    a, ch, c, q, _ = adapter(defer_disposal=True)
    first(a, ch)
    assert c.status()["pending_count"] == 1
    a.abort("source retired")
    assert c.fault is None and not q.closed and not ch.acks
    assert c.status()["pending_count"] == 1  # native Submit must drain before Close
    with pytest.raises(RuntimeError, match="source retired"):
        a.pump(now=.02)
    q.abort("fixture cleanup")


def test_channel_fault_latches_real_inflight_custody_before_abort_clears_it():
    from sm64_events.replay.gpuchannel import ChannelStopped, CLOSED
    from sm64_events.replay.gpudiagnostics import failure_snapshot

    a, ch, c, q, _ = adapter(defer_disposal=True)
    first(a, ch)
    fault = ChannelStopped(CLOSED, 0x20000000 | 10022)

    def stopped():
        raise fault

    ch.bridges = stopped
    try:
        with pytest.raises(ChannelStopped) as caught:
            a.pump(now=.02)
        assert caught.value is fault
        captured = failure_snapshot(fault, frontier=a.frontier, adapter=a, output=None)
        current = captured["adapter"]
        assert current["pending"] == current["bridges"] == 0
        held = current["failure_custody"]
        assert held["pending"] == held["bridges"] == 1
        assert held["operation"] == "Submit" and held["inflight"] == 2
        assert not ch.acks and c.fault is None and not q.closed
        a.abort("later cleanup")
        assert a.status()["failure_custody"] == held
    finally:
        q.abort("fixture cleanup")


def test_packet_acceptance_and_later_exact_key_receipt_are_both_required():
    a, ch, c, q, s = adapter()
    first(a, ch)
    original = a._slots[0].bridge
    c.deliver()
    a.pump(now=0.02)
    assert q.order.pending_count == 1 and not ch.acks and a.status()["pending"] == 1
    assert c.history[-1]["op"] == "Poll"
    c.deliver(keys=[receipt()])
    a.pump(now=0.03)
    assert (
        ch.acks == [original] and ch.acks[0] is original and a.status()["pending"] == 0
    )
    q.abort("fixture complete")


@pytest.mark.parametrize(
    "change",
    [
        {"occurrence": 8},
        {"encode_serial": 2},
        {"pts": 1},
        {"nominal_duration": 3001},
        {"force_idr": False},
        {"texture_index": 2},
    ],
)
def test_monotone_but_wrong_bridge_never_reaches_encoder(change):
    a, ch, c, q, s = adapter()
    ch.offer_values = [(7, 1000, 20)]
    ch.bridge_values = [replace(Bridge(0, 17, 7, 1, 0, 3000, True), **change)]
    with pytest.raises(RuntimeError, match="bridge"):
        a.pump(now=0.01)
    assert [x["op"] for x in c.history] == ["Open"] and q.closed and not ch.acks


@pytest.mark.parametrize("code", [9, 10])
def test_no_admission_retains_same_bridge_and_request_until_later_tick(code):
    a, ch, c, q, s = adapter()
    first(a, ch)
    original = c.history[-1]
    c.deliver(code)
    a.pump(now=0.02)
    assert c.history[-1] == original and not ch.acks and not a._slots[0].admitted
    c.deliver(keys=[receipt()])
    a.pump(now=0.03)
    assert len(ch.acks) == 1 and not a.status()["pending"]
    q.abort("fixture complete")


def test_receipt_before_admission_or_wrong_token_aborts_without_metadata_ack():
    a, ch, c, q, s = adapter()
    first(a, ch)
    c.deliver(9, keys=[receipt()])
    with pytest.raises(RuntimeError, match="custody"):
        a.pump(now=0.02)
    assert not ch.acks and q.closed
    a, ch, c, q, s = adapter()
    first(a, ch)
    c.deliver(keys=[receipt(token=99)])
    with pytest.raises(RuntimeError, match="custody"):
        a.pump(now=0.02)
    assert not ch.acks and q.closed


def test_mux_refusal_never_acknowledges_key_even_if_native_returned_it():
    a, ch, c, q, s = adapter()
    first(a, ch)

    def fail(*args, **kwargs):
        raise RuntimeError("fixture mux refused")

    q.complete = fail
    c.deliver(keys=[receipt()])
    with pytest.raises(RuntimeError, match="mux refused"):
        a.pump(now=0.02)
    assert not ch.acks and q.closed and c.fault


def test_heartbeat_waits_for_selection_and_uses_original_retained_source():
    a, ch, c, q, s = adapter()
    first(a, ch)
    s.certificate = 1000.2
    assert a.heartbeat(1000.2, now=0.02) is None
    c.deliver(keys=[receipt()])
    a.pump(now=0.03)
    h = a.heartbeat(1000.2, now=0.04)
    assert (
        h.request.repeat
        and h.request.source.occurrence == 7
        and c.history[-1]["op"] == "Repeat"
    )
    assert c.history[-1]["serial"] == 2 and c.history[-1]["generation"] == 1
    c.deliver()
    a.pump(now=0.05)
    assert len(ch.acks) == 1 and not a.status()["pending"]
    q.abort("fixture complete")


def test_later_bridge_cannot_bypass_earlier_unpublished_selected_request():
    a, ch, c, q, s = adapter()
    ch.offer_values = [(7, 1000, 20), (8, 1000.033, 190)]
    ch.bridge_values = [Bridge(1, 18, 8, 2, 2970, 3000, False)]
    with pytest.raises(RuntimeError, match="oldest unmatched"):
        a.pump(now=0.01)
    assert [x["op"] for x in c.history] == ["Open"] and q.closed


def test_pending_offers_are_retained_with_age_when_decision_capacity_is_full():
    a, ch, c, q, s = adapter(max_pending=1, max_age=0.1)
    ch.offer_values = [(7, 1000, 20), (8, 1000.033, 190)]
    a.pump(now=0.01)
    assert a.status()["pending"] == 1 and a.status()["offers"] == 1
    with pytest.raises(RuntimeError, match="age deadline"):
        a.pump(now=0.2)
    assert q.closed and c.fault


def test_controller_backpressure_does_not_drop_pending_selected_picture():
    a, ch, c, q, s = adapter()
    c.refuse = True
    first(a, ch)
    assert a.status()["pending"] == 1 and a.status()["inflight"] is None
    c.refuse = False
    a.pump(now=0.02)
    assert c.history[-1]["op"] == "Submit"
    q.abort("fixture complete")


def test_helper_fault_retires_media_and_never_mints_receipts():
    a, ch, c, q, s = adapter()
    first(a, ch)
    c.fault = "fixture watchdog failure"
    with pytest.raises(RuntimeError, match="watchdog"):
        a.pump(now=0.02)
    assert q.closed and not ch.acks


def test_wrong_thread_cannot_mutate_or_abort_owner():
    a, ch, c, q, s = adapter()
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(RuntimeError, match="worker"):
            pool.submit(a.pump, now=1).result()
    assert not q.closed
    q.abort("fixture complete")


def test_fault_status_remains_available_for_owner_disposal_handoff():
    a, ch, c, q, s = adapter()
    a.abort("fixture stopped")
    assert (
        a.status()["fault"] == "fixture stopped" and not a.status()["helper_disposed"]
    )


def test_open_command_mutation_cannot_change_expected_reply_kind():
    q = media()
    ch = Channel()
    s = Selection(ch, q)
    c = Controller()
    a = ChannelEncoder(s, c, max_pending=4, max_age=2)
    command = dict(
        op="Open",
        adapter_luid=[0, 84637],
        names=["tex-a", "tex-b"],
        options=dict(width=16, height=16, input_format=1, max_packet_bytes=8192),
    )
    assert a.start(command)
    # The real Controller freezes its wire; emulate it before caller mutation.
    request_id, captured = c.commands.popleft()
    c.commands.append((request_id, dict(captured)))
    command["op"] = "Repeat"
    c.deliver()
    a.pump(now=0)
    assert a.opened and not q.closed
    q.abort("fixture complete")


@pytest.mark.parametrize("late_kind", ["packet", "custody"])
def test_expired_pending_cannot_be_erased_by_a_late_success(late_kind):
    a, ch, c, q, s = adapter(max_age=0.1)
    first(a, ch)
    if late_kind == "custody":
        c.deliver()
        a.pump(now=0.02)
    c.deliver(keys=[receipt()])
    with pytest.raises(RuntimeError, match="age deadline"):
        a.pump(now=0.2)
    assert not ch.acks and q.closed and a.status()["fault"]


def bootstrap():
    q = media()
    ch = Channel()
    s = Selection(ch, q)
    c = Controller()
    a = ChannelEncoder(s, c, max_pending=4, max_age=2)
    command = dict(
        op="Open",
        adapter_luid=[0, 84637],
        names=["tex-a", "tex-b"],
        options=dict(width=16, height=16, input_format=1, max_packet_bytes=8192),
    )
    ident = c.enqueue(command)
    c.deliver()
    reply = c.take_result()
    reply.command_json = json.dumps(
        command,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    reply.metadata.update(
        nonce=c.nonce,
        request_id=ident,
        status=dict(
            state=1,
            adapter_high=0,
            adapter_low=84637,
            submitted=0,
            completed=0,
            delivered=0,
            acquired=0,
            released=0,
            held_mask=0,
            pending_mask=0,
        ),
    )
    return a, ch, c, q, s, command, ident, reply


def test_attach_initial_open_retains_first_batch_without_reopening():
    a, ch, c, q, s, command, ident, reply = bootstrap()
    a.attach_open(command, ident, reply, initial_offers=[(7, 1000, 20)], now=0)
    ch.bridge_values = [Bridge(0, 17, 7, 1, 0, 3000, True)]
    a.pump(now=0.01)
    assert [x["op"] for x in c.history] == ["Open", "Submit"] and a.status()[
        "offers"
    ] == 0
    q.abort("fixture complete")


@pytest.mark.parametrize(
    "change", ["nonce", "request", "used", "pending", "retained", "packet", "adapter"]
)
def test_attach_rejects_wrong_or_used_helper_epoch(change):
    a, ch, c, q, s, command, ident, reply = bootstrap()
    if change == "nonce":
        reply.metadata["nonce"] = "another"
    elif change == "request":
        ident = 2
    elif change == "used":
        c.next = 3
    elif change == "pending":
        c.enqueue(dict(op="Poll"))
    elif change == "retained":
        reply.metadata["retained"]["valid"] = 1
    elif change == "packet":
        reply.payload = b"unexpected"
    elif change == "adapter":
        command["adapter_luid"] = [0, 99]
    with pytest.raises((ValueError, RuntimeError)):
        a.attach_open(command, ident, reply, initial_offers=[], now=0)
    assert q.closed and c.fault and not a.started


@pytest.mark.parametrize("change", ["textures", "options", "adapter"])
def test_attach_command_is_bound_to_actual_admitted_open(change):
    a, ch, c, q, s, command, ident, reply = bootstrap()
    actual = json.loads(reply.command_json)
    if change == "textures":
        actual["names"] = ["other-texture-a", "other-texture-b"]
    elif change == "options":
        actual["options"]["width"] = 32
    else:
        reply.metadata["status"]["adapter_low"] = 99
    reply.command_json = json.dumps(
        actual,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    with pytest.raises(RuntimeError, match="sole completed initial Open"):
        a.attach_open(command, ident, reply, initial_offers=[(7, 1000, 20)], now=0)
    assert q.closed and c.fault and not a.opened

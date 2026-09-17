import json
import pytest
from sm64_events.replay.packetorder import (
    ExistingTickAllocator,
    OrderedPackets,
    Ticket,
    Refused,
    PacketReady,
)
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.feedmap import feed_map


@pytest.fixture
def make():
    def create(**limits):
        run = MediaRun("run-A", 1000.0)
        return OrderedPackets(
            run, ExistingTickAllocator(run), encoder_duration=3000, **limits
        )

    return create


def offer(q, number, stamp, frame=None, stamps=None, **options):
    result = q.offer(
        number,
        1000 + stamp,
        number if frame is None else frame,
        stamps or {"pad": number},
        packet_limit=32,
        now=options.pop("now", 0),
        **options,
    )
    assert isinstance(result, Ticket), result
    return result


def encoded(q, ticket):
    request = q.take_encode()
    assert request.ticket == ticket
    assert q.complete(
        ticket,
        f"picture{request.source.occurrence}".encode(),
        pts=request.pts,
        duration=q.encoder_duration,
        keyframe=False,
    )
    return request


def consume(q):
    result = []
    while item := q.take_disposition():
        result.append((None, item))
    while item := q.take_packet():
        if isinstance(item, PacketReady):
            result.append((item, q.ack_muxed(item.ticket)))
        else:
            result.append((None, item))
    return result


def test_delayed_head_and_reordered_completion_cannot_bypass_frontier(make, tmp_path):
    q = make(max_age=10)
    a = offer(q, 1, 0)
    b = offer(q, 2, 0.033333)
    c = offer(q, 3, 0.066667)
    q.resolve(b, "selected")
    assert q.take_encode() is None
    q.resolve(a, "selected")
    ra = q.take_encode()
    rb = q.take_encode()
    assert (ra.ticket, rb.ticket) == (a, b)
    q.complete(b, b"newer", pts=rb.pts, duration=q.encoder_duration, keyframe=False)
    q.advance_frontier(1003)
    assert isinstance(q.heartbeat(1003, packet_limit=32, now=3), Refused)
    assert q.take_packet() is None and not q.expire(3)
    q.resolve(c, "selected")
    rc = q.take_encode()
    # B's packet is complete with a known end, but A still owns the head.
    assert q.take_packet() is None
    q.complete(a, b"older", pts=ra.pts, duration=q.encoder_duration, keyframe=False)
    first = q.take_packet()
    assert first.source.occurrence == 1 and first.duration == 3000
    assert q.pending_count == 3  # offering to mux is NOT acknowledgment
    q.ack_muxed(first.ticket)
    second = q.take_packet()
    assert second.source.occurrence == 2 and second.pts == 3000
    q.ack_muxed(second.ticket)
    assert q.take_packet() is None
    q.complete(c, b"last", pts=rc.pts, duration=q.encoder_duration, keyframe=False)
    assert q.seal(1003)
    last, ack = consume(q)[0]
    assert (last.pts, last.duration, last.source.occurrence) == (6000, 264000, 3)
    assert q.pending_count == q.pending_bytes == 0 and ack.kind == "muxed"
    (tmp_path / "delayed-head.json").write_bytes(
        json.dumps(
            {
                "delay_seconds": 3,
                "heartbeat_refused": True,
                "packet_order": [1, 2, 3],
                "pts": [0, 3000, 6000],
                "duration": [3000, 3000, 264000],
            }
        ).encode()
    )


def test_repeated_game_counter_does_not_replace_occurrence_or_frozen_pad(make):
    q = make()
    stamps = {"pad": {"x": 11, "buttons": ["A"]}}
    a = offer(q, 1, 0, 100, stamps)
    stamps["pad"]["x"] = 99
    b = offer(q, 2, 0.04, 0, {"pad": {"x": 22}})
    c = offer(q, 3, 0.08, 100, {"pad": {"x": 33}})
    for ticket in [a, b, c]:
        q.resolve(ticket, "selected")
        encoded(q, ticket)
    q.advance_frontier(1000.2)
    q.seal(1000.2)
    outputs = consume(q)
    assert [p.source.frame for p, _ in outputs] == [100, 0, 100]
    assert [p.source.stamps()["pad"]["x"] for p, _ in outputs] == [11, 22, 33]
    rows = [
        {"ts": p.source.capture_ts, "frame": p.source.frame, **p.source.stamps()}
        for p, _ in outputs
    ]
    ledger = PictureLedger()
    for p, _ in outputs:
        ledger.mark_fed(
            p.source.capture_ts,
            q.run.origin_ts + p.pts / 90000,
            media_run=q.run,
            pts=p.pts,
        )
    values, _, stats = feed_map(
        [p.pts for p, _ in outputs],
        q.run.id,
        rows,
        ledger.feeds_between(999, 1002),
        lambda row: row["pad"]["x"],
    )
    assert values == [11, 22, 33] and stats["matched"] == 3


def test_explicit_coalescing_extends_original_hold_without_gap(make):
    q = make()
    a = offer(q, 1, 0)
    q.resolve(a, "selected")
    encoded(q, a)
    b = offer(q, 2, 0.01)
    q.resolve(b, "coalesced", retained=a, reason="exact stride8 sample equality")
    c = offer(q, 3, 0.06)
    q.resolve(c, "selected")
    encoded(q, c)
    q.advance_frontier(1000.1)
    q.seal(1000.1)
    out = consume(q)
    assert [ack.kind for _, ack in out] == ["coalesced", "muxed", "muxed"]
    assert out[1][0].duration == 5400 and out[0][1].retained == a
    assert [p.source.occurrence for p, _ in out if p] == [1, 3]


@pytest.mark.parametrize(
    "limits", [dict(max_count=2, max_bytes=10000), dict(max_count=100, max_bytes=82)]
)
def test_bounded_refusal_and_explicit_age_failure(make, limits):
    q = make(max_age=2, **limits)
    a = offer(q, 1, 0)
    b = offer(q, 2, 0.03)
    before = (q.pending_count, q.pending_bytes)
    for _ in range(100):
        assert q.offer(3, 1000.06, 3, {"pad": 3}, packet_limit=32, now=0) == Refused(
            "capacity"
        )
    assert (q.pending_count, q.pending_bytes) == before
    q.resolve(b, "selected")
    assert q.expire(2.1)
    outcomes = [ack for _, ack in consume(q)]
    assert len(outcomes) == 2 and all(
        ack.kind == "failed" and ack.reason == "pending age limit" for ack in outcomes
    )
    assert q.pending_bytes == q.pending_count == 0 and q.closed
    assert not q.complete(
        a, b"late", pts=0, duration=q.encoder_duration, keyframe=False
    )


@pytest.mark.parametrize("invalid", ["pts", "bytes"])
def test_bad_encoder_completion_aborts_pending_reference_chain(make, invalid):
    q = make()
    a = offer(q, 1, 0)
    b = offer(q, 2, 0.03)
    for ticket in [a, b]:
        q.resolve(ticket, "selected")
    request = q.take_encode()
    q.take_encode()
    assert not q.complete(
        a,
        b"x" * 33 if invalid == "bytes" else b"x",
        pts=1 if invalid == "pts" else request.pts,
        duration=q.encoder_duration,
        keyframe=False,
    )
    assert all(ack.kind == "failed" for _, ack in consume(q)) and q.pending_bytes == 0


def test_heartbeat_needs_frontier_and_retains_original_source(make):
    q = make()
    a = offer(q, 1, 0, 77, {"pad": "A"})
    q.resolve(a, "selected")
    encoded(q, a)
    assert q.heartbeat(1001, packet_limit=32, now=1) == Refused(
        "capture frontier unproven"
    )
    q.advance_frontier(1001)
    h = q.heartbeat(1001, packet_limit=32, now=1)
    assert isinstance(h, Ticket)
    request = encoded(q, h)
    assert (
        request.repeat
        and request.source.capture_ts == 1000
        and request.source.occurrence == 1
    )
    original = q.take_packet()
    q.ack_muxed(original.ticket)
    assert original.duration == 90000
    assert not q.seal(1002)
    q.advance_frontier(1002)
    assert q.seal(1002)
    repeat, ack = consume(q)[0]
    assert repeat.repeat and ack.retained == a and repeat.pts == 90000
    ledger = PictureLedger()
    ledger.mark_fed(1000, 1000, media_run=q.run, pts=0)
    ledger.mark_fed(None, 1001, media_run=q.run, pts=90000)
    values, repeats, _ = feed_map(
        [0, 90000],
        q.run.id,
        [{"ts": 1000, "frame": 77}],
        ledger.feeds_between(999, 1003),
        lambda row: row["frame"],
    )
    assert values == [77, 77] and repeats == [False, True]


def test_final_completion_after_pause_has_known_hold_without_future_frame(make):
    q = make()
    a = offer(q, 1, 0)
    q.advance_frontier(1000.25)
    assert not q.seal(1000.25)
    q.resolve(a, "selected")
    request = q.take_encode()
    assert q.seal(1000.25)
    assert q.take_packet() is None
    q.complete(
        a, b"last-picture", pts=request.pts, duration=q.encoder_duration, keyframe=False
    )
    packet, ack = consume(q)[0]
    assert (
        packet.duration == 22500
        and packet.source.occurrence == 1
        and ack.kind == "muxed"
    )


def test_repeated_capture_time_is_distinct_pts_but_legacy_row_join_stays_unknown(make):
    q = make()
    a = offer(q, 1, 0, 7)
    b = offer(q, 2, 0, 8)
    for ticket in [a, b]:
        q.resolve(ticket, "selected")
        encoded(q, ticket)
    q.advance_frontier(1000.1)
    q.seal(1000.1)
    out = consume(q)
    assert [p.pts for p, _ in out] == [0, 1]
    assert [p.source.frame for p, _ in out] == [7, 8]
    values, _, stats = feed_map(
        [0, 1],
        q.run.id,
        [{"ts": 1000, "frame": 7}, {"ts": 1000, "frame": 8}],
        [
            {"run_id": q.run.id, "pts": 0, "ts": 1000},
            {"run_id": q.run.id, "pts": 1, "ts": 1000},
        ],
        lambda row: row["frame"],
    )
    assert (
        values is None and stats["unmatched"] == 2
    )  # never fake a unique timestamp row


def test_removing_pending_head_guard_is_detected_by_order_oracle(make):
    q = make()
    tickets = [offer(q, i + 1, i * 0.04) for i in range(3)]
    for t in tickets:
        q.resolve(t, "selected")
    requests = [q.take_encode() for _ in tickets]
    q.complete(
        tickets[1],
        b"newer",
        pts=requests[1].pts,
        duration=q.encoder_duration,
        keyframe=False,
    )
    assert q.take_packet() is None
    q._order.rotate(-1)  # Negative control: bypass the original unresolved head.
    escaped = q.take_packet()
    assert escaped.source.occurrence == 2 and escaped.pts == 3600
    assert escaped.source.occurrence != requests[0].source.occurrence


def test_sustained_coalescing_releases_reservations_without_publishing_or_ticking(make):
    q = make(max_count=2, max_bytes=82)
    a = offer(q, 1, 0)
    q.resolve(a, "selected")
    encoded(q, a)
    encoded_bytes = q.pending_bytes  # the real packet plus stamps, not packet_limit
    for i in range(2, 1002):
        b = q.offer(i, 1000 + i * 0.01, i, {"pad": 1}, packet_limit=32, now=i * 0.01)
        assert isinstance(b, Ticket)
        q.resolve(b, "coalesced", retained=a, reason="exact sample equality")
        assert q.take_packet() is None
        ack = q.take_disposition()
        assert (ack.kind, ack.retained) == ("coalesced", a)
        assert q.pending_count == 1 and q.pending_bytes == encoded_bytes
        assert q.allocate.last_pts == 0 and q.take_encode() is None
    q.advance_frontier(1011)
    heartbeat = q.heartbeat(1011, packet_limit=32, now=11)
    assert isinstance(heartbeat, Ticket)
    encoded(q, heartbeat)
    original = q.take_packet()
    assert original.source.occurrence == 1 and original.duration == 990000
    q.ack_muxed(original.ticket)
    q.advance_frontier(1012)
    assert q.seal(1012)
    repeat, _ = consume(q)[0]
    assert repeat.repeat and repeat.source.occurrence == 1


def test_unresolved_selection_head_prevents_coalescing_receipt_escape(make):
    q = make()
    a = offer(q, 1, 0)
    b = offer(q, 2, 0.01)
    q.resolve(b, "coalesced", retained=a, reason="exact sample equality")
    assert q.take_disposition() is None
    q.resolve(a, "selected")
    ack = q.take_disposition()
    assert ack.ticket == b and ack.retained == a and q.pending_count == 1


def test_failure_invalidates_queued_disposition_once(make):
    q = make()
    a = offer(q, 1, 0)
    b = offer(q, 2, 0.01)
    q.resolve(a, "selected")
    q.resolve(b, "coalesced", retained=a, reason="exact sample equality")
    assert q.fail(a, "native completion failed")
    assert q.take_disposition() is None
    out = consume(q)
    assert [ack.ticket for _, ack in out] == [a, b]
    assert all(ack.kind == "failed" for _, ack in out) and q.pending_count == 0


def test_exact_native_witness_pts_durations_and_keyflags_are_preserved(make):
    q = make()
    input_ticks = [0, 3001, 3001, 179999, 180000, 720000, 720000, 720000, 720000]
    expected = [0, 3001, 3002, 179999, 180000, 720000, 720001, 720002, 720003]
    keys = [True, False, False, False, True, True, True, True, False]
    for i, tick in enumerate(input_ticks):
        ticket = offer(q, i + 1, tick / 90000)
        q.resolve(ticket, "selected")
        request = q.take_encode()
        assert q.complete(
            ticket,
            bytes([i]),
            pts=request.pts,
            duration=q.encoder_duration,
            keyframe=keys[i],
        )
    q.advance_frontier(1000 + 729003 / 90000)
    assert q.seal(q.frontier)
    outputs = consume(q)
    assert [p.pts for p, _ in outputs] == expected
    assert [p.duration for p, _ in outputs] == [
        3001,
        1,
        176997,
        1,
        540000,
        1,
        1,
        1,
        9000,
    ]
    assert [p.keyframe for p, _ in outputs] == keys
    assert not q.seal(1010)  # A delivered end cannot be retimed afterward.


def test_existing_selector_keeps_a_folded_sample_distinct_from_its_retained_row():
    import numpy as np
    from sm64_events.replay.pixels import sample_bytes
    from sm64_events.replay.ledger import SAMPLE_STRIDE

    ledger = PictureLedger()
    a = np.zeros((8, 8, 4), np.uint8)
    b = np.full_like(a, 99)
    assert ledger.observe(a, 1000, 10)
    assert not ledger.observe(b, 1000.01, 10)  # Fold: comparison baseline is now B.
    assert not ledger.observe(b, 1000.04, 11)  # Equal to B despite different counter.
    assert (
        len(ledger._rows) == 1 and ledger._rows[0][0] == 1000
    )  # Retained picture is A.
    assert ledger._prev_sample == sample_bytes(b, SAMPLE_STRIDE)


def test_new_run_accepts_repeated_native_occurrence_but_rejects_old_completion(make):
    old = make()
    old_ticket = offer(old, 1, 0, 9)
    old.resolve(old_ticket, "selected")
    old_request = old.take_encode()
    run = MediaRun("run-B", 1000.0)
    new = OrderedPackets(run, ExistingTickAllocator(run), encoder_duration=3000)
    ticket = offer(new, 1, 0, 9)
    new.resolve(ticket, "selected")
    request = new.take_encode()
    assert (ticket.serial, old_ticket.serial) == (1, 1) and ticket.run != old_ticket.run
    assert not new.complete(
        old_ticket, b"stale", pts=old_request.pts, duration=3000, keyframe=True
    )
    assert new.complete(
        ticket, b"current", pts=request.pts, duration=3000, keyframe=True
    )
    new.advance_frontier(1000.1)
    assert new.seal(1000.1)
    packet, ack = consume(new)[0]
    assert packet.payload == b"current" and ack.ticket.run == "run-B"


def test_encoder_nominal_echo_is_independent_of_visible_hold(make):
    q = make()
    a = offer(q, 1, 0)
    q.resolve(a, "selected")
    request = q.take_encode()
    assert request.encoder_duration == 3000
    q.advance_frontier(1005)
    assert q.seal(1005)
    assert q.complete(a, b"picture", pts=0, duration=3000, keyframe=True)
    assert q.take_packet().duration == 450000


def test_wrong_nominal_duration_ends_dependent_run(make):
    q = make()
    a = offer(q, 1, 0)
    q.resolve(a, "selected")
    q.take_encode()
    assert not q.complete(a, b"picture", pts=0, duration=1, keyframe=True)
    assert q.closed and q.fault == "encoder nominal duration mismatch"
    assert q.take_packet().kind == "failed"


def test_foreign_worker_cannot_mutate_pending_identity(make):
    from concurrent.futures import ThreadPoolExecutor

    q = make()
    with ThreadPoolExecutor(max_workers=1) as other:
        result = other.submit(q.offer, 1, 1000, 1, {}, packet_limit=32, now=0)
        with pytest.raises(RuntimeError, match="media worker"):
            result.result()
    assert q.pending_count == q.pending_bytes == 0


def test_an_encoded_packet_returns_its_unused_reservation(make):
    """A seat is reserved at the encoder's worst case (packet_limit) because
    the packet size is unknown until it returns. Once it has returned, the
    seat shrinks to the real packet: a picture waiting for the mux must not
    hold a whole packet_limit against the next offer (round 48: three
    pictures in flight out of eight native slots)."""
    q = make(max_count=8, max_bytes=32 * 8)
    first = offer(q, 1, 0)
    reserved = q.pending_bytes
    assert reserved > 32  # the packet limit plus the stamps
    q.resolve(first, "selected")
    request = q.take_encode()
    assert q.complete(first, b"\x01\x02\x03", pts=request.pts,
                      duration=q.encoder_duration, keyframe=True)
    assert q.pending_bytes == reserved - 32 + 3

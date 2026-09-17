"""Native channel values use the original RAM decoder and single QPC anchor."""

from dataclasses import replace
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from sm64_events.memory.layout import layout_for
from sm64_events.replay import gpuchannel as G
from sm64_events.replay.clock import CaptureClock
from sm64_events.replay.gpuinput import OfferDecoder, ChannelSelection
from sm64_events.replay.pluginsource import table_for
from test_gpu_channel import native, Host  # noqa: F401 -- shared real x86 fixture
from test_gpumedia import media, complete
from test_pluginsource import rdram_with, raw_table


@pytest.fixture(scope="module")
def header(native):
    with Host(native, 17, 19) as host, host.client() as client:
        result = client.header
    # This probe changes only the decoder's owned header copy, never the shared
    # mapping or installed plugin. Wire layout/publication has its own real host.
    table = table_for(layout_for("us"))
    result.table_count = len(table)
    for index in range(16):
        result.table[index] = (
            G.Field(*table[index][1:]) if index < len(table) else G.Field()
        )
    G.validate_header(result, result.total_bytes)
    return result


def decoder(header):
    clock = CaptureClock(10_000_000, datetime.fromtimestamp(1000, timezone.utc))
    return OfferDecoder(header, layout_for("us"), clock)


def offer(d, token=1, occurrence=1, frame=100, delta=0, color=7, missing=()):
    memory = rdram_with(d.layout, frame=frame)
    rows = raw_table(memory, d.table)
    for index in missing:
        rows[index] = b""
    lengths = tuple(map(len, rows)) + (0,) * (16 - len(rows))
    qpc = d.header.qpc_frequency + delta
    return G.Offer(
        (token - 1) % 8,
        token,
        occurrence,
        qpc - 1,
        qpc,
        17,
        19,
        0x123400,
        1,
        1,
        lengths,
        b"".join(rows),
        bytes([color, 4, 8, 255]) * 9,
    )


def test_original_decoder_clock_and_odd_geometry(header):
    d = decoder(header)
    value = offer(d, frame=1234, delta=1234567)
    actual = d.decode(value)
    assert actual.picture.shape == (19, 17, 4)
    assert actual.picture.sample is value.sample
    assert len(actual.picture.sample) == 36
    assert actual.stamp.frame == 1234 and actual.stamp.igt_overall == 77
    assert (
        actual.stamp.pad.stick_x,
        actual.stamp.pad.stick_y,
        actual.stamp.pad.buttons,
    ) == (12, -34, 0x8000)
    assert actual.stamp.list_qpc == value.list_qpc
    assert actual.stamp.present_qpc == value.boundary_qpc
    assert actual.stamp.extras()["exact"]
    assert (
        actual.capture_ts
        == d.clock.utc_of(
            value.boundary_qpc * 10_000_000 // header.qpc_frequency
        ).timestamp()
    )
    # The descriptor is an immutable copy even if its caller later changes RAM schema.
    owned = G.Header.from_buffer_copy(bytes(header))
    pinned = decoder(owned)
    owned.table[0].offset += 4
    assert pinned.decode(value).stamp.frame == 1234


def test_packed_missing_rows_do_not_shift_igt_or_invent_input(header):
    d = decoder(header)
    partial = d.decode(offer(d, missing=(1, 2)))
    assert partial.stamp.frame == 100 and partial.stamp.pad is None
    assert partial.stamp.igt_overall == 77
    assert d.decode(offer(d, missing=(0,))).stamp is None
    many = d.decode(replace(offer(d), lists_since=2))
    assert many.stamp.extras()["exact"] is False


@pytest.mark.parametrize(
    "change", ["table", "length", "tail", "geometry", "outcome", "qpc"]
)
def test_corrupt_source_metadata_is_refused(header, change):
    h = G.Header.from_buffer_copy(bytes(header))
    if change == "table":
        h.table[0].offset += 4
        with pytest.raises(G.ProtocolError, match="ROM layout"):
            decoder(h)
        return
    d = decoder(h)
    value = offer(d)
    if change == "length":
        value = replace(
            value, lengths=(3,) + value.lengths[1:], stamp_bytes=value.stamp_bytes[1:]
        )
    if change == "tail":
        value = replace(value, stamp_bytes=value.stamp_bytes + b"unexpected")
    if change == "geometry":
        value = replace(value, width=16)
    if change == "outcome":
        value = replace(value, outcome=4)
    if change == "qpc":
        value = replace(value, list_qpc=value.boundary_qpc + 1)
    with pytest.raises(G.ProtocolError):
        d.decode(value)


class Client:
    def __init__(self):
        self.replies = []
        self.certificate = None

    def reply(self, source, kind, **kwargs):
        self.replies.append((source, kind, kwargs))

    def frontier(self):
        return self.certificate


def test_selection_preserves_source_ids_reset_counters_and_certified_frontier(header):
    d, c = decoder(header), Client()
    q = media(source_namespace=d.namespace)
    selection = ChannelSelection(c, d, q)
    freq = header.qpc_frequency
    a = offer(d, frame=900)
    b = offer(d, 2, 7, frame=900, delta=freq // 30, color=8)
    reset = offer(d, 3, 8, frame=1, delta=freq // 15, color=9)
    same_time = offer(d, 4, 9, frame=2, delta=freq // 15, color=10)
    folded = offer(d, 5, 10, frame=2, delta=freq // 10, color=10)
    decisions = [
        selection.handle(o, now=50 + i)
        for i, o in enumerate((a, b, reset, same_time, folded))
    ]
    assert [x.kind for x in decisions] == [
        "selected",
        "selected",
        "selected",
        "selected",
        "coalesced",
    ]
    assert [x.request.source.frame for x in decisions[:4]] == [900, 900, 1, 2]
    assert [x.request.source.stamps()["source_id"] for x in decisions[:4]] == [
        f"{d.namespace}:{i}" for i in (1, 7, 8, 9)
    ]
    assert c.replies[4][2] == dict(
        retained_serial=decisions[3].ticket.serial, retained_occurrence=9
    )
    assert c.replies[0][2]["force_idr"] is True
    assert c.replies[1][2]["force_idr"] is False
    assert selection.advance_frontier() is None
    c.certificate = (a.boundary_qpc, 1, 1)
    assert (
        selection.advance_frontier() is None
    )  # old producer status after newer offers
    c.certificate = (
        freq * 2,
        12,
        5,
    )  # can include explicit native no-image occurrences
    frontier = selection.advance_frontier()
    assert 0 < 1001-frontier < 1e-6
    assert q.order.frontier == frontier
    for item in decisions[:4]:
        complete(q, item)
    q.abort("fixture complete")


def test_source_order_and_unseen_frontier_fail_run_without_guessing(header):
    d, c = decoder(header), Client()
    q = media(source_namespace=d.namespace)
    selection = ChannelSelection(c, d, q)
    selection.handle(offer(d), now=0)
    c.certificate = (header.qpc_frequency * 2, 2, 2)
    with pytest.raises(G.ProtocolError, match="frontier"):
        selection.advance_frontier()
    assert q.closed and q.fault
    q = media(source_namespace=d.namespace)
    selection = ChannelSelection(c, d, q)
    with pytest.raises(G.ProtocolError, match="order"):
        selection.handle(offer(d, token=2), now=0)
    assert q.closed and not c.replies[1:]


def test_wrong_worker_and_source_session_are_refused(header):
    d, c = decoder(header), Client()
    q = media(source_namespace=d.namespace)
    selection = ChannelSelection(c, d, q)
    with ThreadPoolExecutor(1) as pool:
        with pytest.raises(RuntimeError, match="worker"):
            pool.submit(selection.handle, offer(d), now=0).result()
    assert not q.closed and not c.replies
    with pytest.raises(ValueError, match="another source"):
        ChannelSelection(c, d, media())
    q.abort("fixture complete")


@pytest.mark.parametrize("failure", ["read", "qpc", "media"])
def test_frontier_failures_abort_without_committing_the_certificate(header, failure):
    d, c = decoder(header), Client()
    q = media(source_namespace=d.namespace)
    selection = ChannelSelection(c, d, q)
    selection.handle(offer(d), now=0)

    def fail(*_):
        raise OSError("fixture frontier failure")

    c.certificate = (header.qpc_frequency * 2, 1, 1)
    if failure == "read":
        c.frontier = fail
    elif failure == "qpc":
        c.certificate = (2**63, 1, 1)
    else:
        q.frontier = fail
    with pytest.raises((OSError, G.ProtocolError)):
        selection.advance_frontier()
    assert q.closed and q.fault and selection.frontier_qpc == 0


def test_later_qpc_in_same_utc_microsecond_remains_a_real_source_picture(header):
    h = G.Header.from_buffer_copy(bytes(header))
    h.qpc_frequency = 10_000_000
    d, c = decoder(h), Client()
    q = media(source_namespace=d.namespace)
    selection = ChannelSelection(c,d,q)
    selection.handle(offer(d),now=0)
    c.certificate = (h.qpc_frequency+1,1,1)
    assert selection.advance_frontier() is None  # same rounded time as admitted picture
    c.certificate = (h.qpc_frequency+11,1,1)
    assert selection.advance_frontier() is not None
    later = offer(d,2,2,frame=101,delta=12,color=8)
    assert d.timestamp(c.certificate[0]) == d.decode(later).capture_ts
    result = selection.handle(later,now=.001)
    assert result.kind == "selected" and result.request.source.frame == 101
    assert result.request.source.capture_ts > q.order.frontier
    q.abort("fixture complete")

import pytest
from test_channelencoder import Channel, Controller, Selection, receipt
from test_gpumedia import media
from sm64_events.replay.gpuchannel import Bridge
from sm64_events.replay.channelencoder import ChannelEncoder


@pytest.mark.parametrize("resource", ["bytes", "count"])
def test_defer_offer_before_selection_until_real_media_budget_is_available(resource):
    packet_limit = 1024 * 1024 if resource == "bytes" else 1024
    expected = 3 if resource == "bytes" else 2
    byte_limit = 4 * 1024 * 1024 if resource == "bytes" else 65536
    q = media(
        packet_limit=packet_limit,
        pending_count=8 if resource == "bytes" else 2,
        pending_bytes=byte_limit,
    )
    ch = Channel()
    c = Controller()
    selection = Selection(ch, q)
    seen = []
    original = selection.handle

    def handle(offer, *, now):
        seen.append(offer[0])
        return original(offer, now=now)

    selection.handle = handle
    a = ChannelEncoder(selection, c, max_pending=8, max_age=2)
    command = dict(
        op="Open",
        adapter_luid=[0, 84637],
        names=["tex-a", "tex-b"],
        options=dict(
            width=16, height=16, input_format=1, max_packet_bytes=packet_limit
        ),
    )
    assert a.start(command)
    c.deliver()
    a.pump(now=0)
    try:
        ch.offer_values = [
            (i + 1, 1000 + i * 0.033, (i + 1) * 40) for i in range(expected + 1)
        ]
        ch.bridge_values = [Bridge(0, 17, 1, 1, 0, 3000, True)]
        a.pump(now=0.01)
        assert not q.closed and not q.fault and seen == list(range(1, expected + 1))
        assert a.status()["offers"] == 1 and q.order.pending_count == expected
        assert not q.can_offer and q.order.pending_bytes < byte_limit
        # Accepting the first real packet closes/muxes its known interval and
        # returns actual media credit; original fourth/third offer is then used once.
        c.deliver(keys=[receipt()])
        a.pump(now=0.02)
        assert not q.closed and seen == list(range(1, expected + 2))
        assert not a.status()["offers"] and q.order.pending_count == expected
        assert q.order.pending_bytes <= byte_limit and len(ch.acks) == 1
        assert q.mux.video[0].occurrence == 1
    finally:
        if not a.fault:
            a.abort("owned admission fixture complete")

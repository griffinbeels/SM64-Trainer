"""The Game version setting can name a ROM whose layout is not verified yet.

feature/game-version lets him set JP (or AUTO with a JP ROM loaded);
feature/version-sync ships JP's layout empty until the sync run fills it.
The two met at boot on 2026-08-15: `SnapshotReader` refuses an incomplete
layout (by design -- never read address 0), and `main.build()` constructed
it unguarded, so choosing JP in Settings would have killed the next start.
The rule: an incomplete layout HOLDS the poller (nothing read, nothing
journaled, /health says why and names the fix) -- it never crashes the
server and never falls back to reading US addresses off a JP ROM.
"""
from sm64_events.core.snapshot import UnreadyReader, reader_for
from sm64_events.memory.base import MemoryReadError
from sm64_events.memory.buffer import BufferMemory
from sm64_events.memory.layout import LAYOUT_ROWS, layout_for


def test_a_complete_layout_gives_the_real_reader():
    reader = reader_for(BufferMemory(), "us")
    assert not isinstance(reader, UnreadyReader)
    assert reader.layout is layout_for("us")


def test_an_incomplete_layout_gives_an_unready_reader_that_names_the_fix():
    reader = reader_for(BufferMemory(), "jp")
    assert isinstance(reader, UnreadyReader)
    assert "jp" in reader.reason and "sync_version.py --version jp" in reader.reason
    assert reader.missing == layout_for("jp").missing()
    assert len(reader.missing) == len(LAYOUT_ROWS)
    try:
        reader.read()
    except MemoryReadError as error:
        assert "sync_version.py" in str(error)
    else:
        raise AssertionError("an unready reader must refuse to read")


def test_the_poller_holds_on_an_unready_reader_and_health_says_why():
    """Through the real composition: build_app-level wiring is exercised by
    the poller + /health, not by reasoning about them."""
    import asyncio

    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller

    class Mem(BufferMemory):
        attached = True

        def attach(self):
            return True

        def detach(self):
            pass

    poller = Poller(Mem(), [], Broadcaster(), reader=reader_for(Mem(), "jp"))
    assert poller.hold_reason and "jp" in poller.hold_reason
    asyncio.run(poller.tick())          # a tick must not raise
    assert poller.latest is None        # and must read nothing

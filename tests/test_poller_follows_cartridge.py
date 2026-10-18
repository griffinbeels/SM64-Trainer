"""The layout follows the cartridge (task 0064).

His words: "if the user swaps between the US Usamune rom and the JP usamune
rom, again, it should just work", and "No attempts, PBs or publishable ghosts
from an unverified ROM/layout." The poller identifies the cartridge before it
samples or serves anything; a practice ROM of another region swaps the reader
to that build's layout, and a build whose layout is not verified holds, reading
nothing, exactly like a real run on another ROM.
"""
import asyncio

from sm64_events.core.snapshot import UnreadyReader
from sm64_events.server.poller import Poller
from test_poller import CartridgeMemory, RecordingBroadcaster, rom_header, snap

US = rom_header(b"SM64 USAMUNE v1.93u", b"E")
JP = rom_header(b"SM64 USAMUNE v1.93j", b"J")


class VersionReader:
    """A reader for one build: counts its reads, ticks its own timer."""

    def __init__(self, version, start=1000):
        self.version, self.timer, self.reads = version, start, 0

    def read(self):
        self.reads += 1
        self.timer += 1
        return snap(self.timer)


class Witness:
    def __init__(self, seen, version):
        self.seen, self.version = seen, version

    def process(self, prev, curr):
        self.seen.append(self.version)
        return []


def harness(header, *, jp_ready, start="us"):
    memory = CartridgeMemory(header)
    memory.attached = True
    readers, changes, gaps, seen = {}, [], [], []
    current = {"version": start}

    def factory(version):
        if version == "jp" and not jp_ready:
            reader = UnreadyReader("jp", ("global_timer",))
        else:
            reader = VersionReader(version)
        readers.setdefault(version, []).append(reader)
        return reader

    def on_version_change(version):
        current["version"] = version
        changes.append(version)

    async def on_gap(reason):
        gaps.append(reason)

    first = factory(start)
    poller = Poller(memory, [Witness(seen, start)], RecordingBroadcaster(), reader=first,
                    reader_factory=factory, on_version_change=on_version_change,
                    detector_factory=lambda: [Witness(seen, current["version"])],
                    on_gap=on_gap)
    poller.PRACTICE_ROM_CHECK_S = 0.0           # identify every tick in the test
    return poller, memory, readers, changes, gaps, seen


def ticks(poller, n):
    for _ in range(n):
        asyncio.run(poller.tick())


def test_a_swap_to_an_unverified_build_holds_and_reads_nothing():
    poller, memory, readers, changes, gaps, seen = harness(US, jp_ready=False)
    ticks(poller, 3)
    assert seen == ["us", "us"] and poller.capture_allowed()

    memory.header = JP
    us_reads = readers["us"][0].reads
    ticks(poller, 1)                               # run() stops ticking once detached
    assert readers["us"][0].reads == us_reads      # nothing read through US addresses
    assert changes == ["jp"] and not memory.attached
    assert gaps == ["the jp ROM is not verified yet"]
    assert poller.hold_reason and not poller.capture_allowed()
    assert seen == ["us", "us"]                    # no detector saw a JP frame
    assert poller.observed_game_version == "jp"


def test_a_swap_back_serves_the_first_build_again():
    poller, memory, readers, changes, gaps, seen = harness(US, jp_ready=False)
    ticks(poller, 2)
    memory.header = JP
    ticks(poller, 1)
    assert not poller._serves_loaded_rom()          # the attach path holds too
    memory.header = US
    memory.attached = True
    assert poller._serves_loaded_rom()
    assert changes == ["jp", "us"] and poller.hold_reason is None
    ticks(poller, 3)
    assert seen[-1] == "us" and poller.capture_allowed()


def test_a_swap_to_a_verified_build_reads_its_layout_with_no_restart():
    poller, memory, readers, changes, gaps, seen = harness(US, jp_ready=True)
    ticks(poller, 3)
    memory.header = JP
    ticks(poller, 4)
    assert changes == ["jp"] and memory.attached
    assert gaps == ["the cartridge changed to jp"]
    assert readers["jp"][0].reads >= 2
    assert seen[-1] == "jp" and poller.capture_allowed()


def test_a_boot_guess_is_corrected_by_the_cartridge():
    """AUTO with no emulator at boot guesses a build; the header decides."""
    poller, memory, readers, changes, gaps, seen = harness(US, jp_ready=False, start="jp")
    assert poller.hold_reason
    assert poller._serves_loaded_rom()
    assert changes == ["us"] and poller.hold_reason is None
    ticks(poller, 3)
    assert seen[-1] == "us"


def test_another_usamune_build_is_not_a_practice_rom():
    """A layout describes one build: a different Usamune version would read
    garbage through it, so it runs as a real run (nothing recorded)."""
    poller, memory, readers, changes, gaps, seen = harness(US, jp_ready=True)
    memory.header = rom_header(b"SM64 USAMUNE v1.92j", b"J")
    ticks(poller, 1)
    assert not memory.attached and changes == [] and gaps == ["not a practice ROM"]

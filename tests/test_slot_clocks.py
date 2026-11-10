"""His session clocks stay his: every Trainer write puts the running game's clocks into the slot's trailer.

Usamune's save copies its state block's +0x90 and +0x94 (frames since power-on, 60 a second) and +0xEC (the game
time his pause screen shows) into the trailer at +0x100, +0x104 and +0x120, and its load puts them back (both
builds' code; a cold boot of each confirmed it, 2026-11-08). A state from another session set his one-hour-old US
game's clock to 11h25 ("we shouldn't copy over the overall session timer clock"). Fake game memory only.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
sys.path.insert(0, str(ROOT / "tests"))
import handoff_try as tool  # noqa: E402
import test_handoff_try as handoff_fakes  # noqa: E402
import test_slot_library as libfakes  # noqa: E402
import test_slot_relocate as world  # noqa: E402
import test_slot_translate as crossed  # noqa: E402
from sm64_events.memory.layout import layout_for  # noqa: E402
from sm64_events.slotlibrary import refit, usamune  # noqa: E402
from sm64_events.slotlibrary.atlas import AtlasStore, to_json  # noqa: E402
from sm64_events.slotlibrary.coursesets import place_states  # noqa: E402
from sm64_events.slotlibrary.store import SlotLibrary  # noqa: E402

CLOCKS = {0x90: 0x26420, 0x94: 0x26424, 0xEC: 0x26440}   # Usamune state block field -> slot offset
LIVE = {0x90: 0x00012345, 0x94: 0x00012345, 0xEC: 0x00009ABC}
STALE = 0x0012D733                                        # 11h25 at 30 a second: another session's


def clocked(game, version: str = "us") -> None:
    settings = layout_for(version).usamune_state_start
    for field, value in LIVE.items():
        game.poke(settings + field, struct.pack(">I", value))


def stale(data: bytes) -> bytes:
    out = bytearray(data)
    for at in CLOCKS.values():
        struct.pack_into(">I", out, at, STALE)
    return bytes(out)


def clocks_of(slot: bytes) -> dict[int, int]:
    return {field: struct.unpack_from(">I", slot, at)[0] for field, at in CLOCKS.items()}


def test_a_kept_state_written_into_a_slot_keeps_his_clocks(tmp_path):
    game = libfakes.FakeGame()
    clocked(game)
    library = SlotLibrary(tmp_path / "library")
    data = stale(libfakes.make_slot(0xD1))
    state = libfakes.keep(library, game, data)
    place_states(game, library, {4: state.id})
    assert clocks_of(game.slot(4)) == LIVE
    rest = [at for at in range(usamune.SLOT_BYTES) if all(not start <= at < start + 4 for start in CLOCKS.values())]
    assert all(game.slot(4)[at] == data[at] for at in rest), "only the clocks differ from the kept state"


def test_a_ghost_lab_write_keeps_his_clocks(tmp_path):
    game = handoff_fakes.FakeGame()
    clocked(game)
    image = handoff_fakes.make_image(game)
    image = tool.SlotImage(**{**image.__dict__, "data": stale(image.data)})
    tool.install(game, image, tmp_path, dry_run=False, write_seed=False)
    slot = game.read(usamune.slot_address(handoff_fakes.INDEX), usamune.SLOT_BYTES)
    assert clocks_of(slot) == LIVE


def test_putting_a_backup_back_keeps_his_clocks(tmp_path):
    game = handoff_fakes.FakeGame()
    handoff_fakes.old_slot(game)
    backup = handoff_fakes.run(game, handoff_fakes.make_image(game), tmp_path)
    clocked(game)                                                # later in his session
    tool.restore(game, backup, tmp_path, dry_run=False)
    slot = game.read(usamune.slot_address(handoff_fakes.INDEX), usamune.SLOT_BYTES)
    assert clocks_of(slot) == LIVE


def test_a_refit_after_a_reset_keeps_his_clocks():
    game = libfakes.FakeGame()
    world.live_world(game)
    clocked(game)
    game.save(0, stale(world.make_slot()))
    report = refit.refit_slots(game)
    assert [o.outcome for o in report.slots] == [refit.REFIT]
    assert clocks_of(game.slot(0)) == LIVE


def test_a_translated_state_keeps_the_running_games_clocks(tmp_path, monkeypatch):
    monkeypatch.setattr(usamune, "build_for", lambda version: crossed.JP if version == "jp" else usamune.US)
    game = crossed.jp_game()
    clocked(game, "jp")
    library = SlotLibrary(tmp_path / "library")
    data = bytearray(stale(crossed.us_slot()))
    struct.pack_into(">I", data, 0x100C, 0)                      # nothing unmapped
    state = library.add(bytes(data), source="captured", fingerprint="a" * 64, rom=usamune.ROM_NAMES["us"])[0]
    atlases = AtlasStore(tmp_path / "atlases", lambda _l, _a, path: path.write_bytes(to_json(crossed.make_atlas())))
    place_states(game, library, {2: state.id}, atlases=atlases)
    assert clocks_of(game.slot(2)) == LIVE

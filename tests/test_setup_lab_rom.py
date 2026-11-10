"""The Ghost Lab's ROM inside the existing setup: found while setup watches the game, one setup-check row, no step.

Griffin's bar (2026-11-09): "download the tool, automatically configure with the wizard, and just be able to
immediately jump into all the functionality". His rule (2026-09-16): no onboarding past the original. So the copy
rides setup's own observation, readiness never reads it, and the pick is one row shown only while the US ROM is
missing. Fakes only: no process, registry or real ROM.
"""
from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sm64_events.core.onboarding import readiness
from sm64_events.core.setup_runtime import SetupRuntime
from sm64_events.scenelab import roms
from sm64_events.server.setup_api import setup_check
from test_lab_roms import FakeProject64, FakeRegistry, rom, store_for
from test_onboarding import installed
from test_setup_profile import US, by_id, fake_folder, fake_registry, layer, profile


class SetupReader(FakeProject64):
    """The setup observer's own reader: attached once per pid, holding Usamune US word-swapped."""

    def __init__(self, image):
        super().__init__(image, pid=123)
        self.attached_to, self.closed = [], False

    def attach(self, pid=None):
        self.attached_to.append(pid)
        return True

    def detach(self):
        self.closed = True


def runtime(store, image=None):
    reader = SetupReader(image if image is not None else rom("us"))
    target = {"state": "ready", "pid": 123}
    recorder = {"recording": True, "frame_source": "plugin", "frame_source_health": {"delivered": 100}}
    poller = SimpleNamespace(input_sampler=SimpleNamespace(health=lambda: {"frames": 100}),
                             latest=SimpleNamespace(global_timer=100), paused=False, hold_reason=None)
    processes = SimpleNamespace(setup_target=lambda: target, check_folder=lambda folder: {"state": "ready", "pid": None})
    observer = SetupRuntime(processes, lambda: reader, poller,
                            SimpleNamespace(recorder=SimpleNamespace(status=lambda: recorder)), lambda: 0.0,
                            rom_store=store)
    return observer, reader


def no_own_reader():
    raise AssertionError("setup's copy must ride setup's reader, never a second attach")


@pytest.fixture
def project64(tmp_path):
    folder = tmp_path / "Project64 1.6"
    (folder / "roms").mkdir(parents=True)
    (folder / "roms" / "SM64_USAMUNE_v1.93j.z64").write_bytes(rom("jp"))
    return folder


def test_setup_copies_the_game_through_its_own_reader_and_finds_the_other_version(tmp_path, project64):
    store = roms.RomStore(tmp_path / "lab" / "roms", pj64_dir=lambda: None, registry=lambda: FakeRegistry(),
                          reader=no_own_reader)
    observer, reader = runtime(store)
    observed = observer(replace(installed(), pj64_dir=str(project64)))
    held = observed["lab_roms"]
    assert held["us"]["found"] and held["us"]["source"] == roms.GAME
    assert held["jp"]["found"] and held["jp"]["source"] == roms.FOLDER
    assert reader.attached_to == [123] and not reader.closed and reader.image_reads == 1
    assert store.rom_for("us").read_bytes() == rom("us")


def test_the_copy_never_changes_what_setup_shows_or_waits_for(tmp_path, project64):
    with_store, _ = runtime(store_for(tmp_path))
    without, _ = runtime(None)
    layer_status = replace(installed(), pj64_dir=str(project64))
    first, second = with_store(layer_status), without(layer_status)
    assert second["lab_roms"] is None
    assert readiness(layer_status, first) == readiness(layer_status, second)
    assert {key: value for key, value in first.items() if key != "lab_roms"} == \
        {key: value for key, value in second.items() if key != "lab_roms"}


def test_a_failing_store_never_breaks_setup(tmp_path, project64):
    store = store_for(tmp_path)

    def full_disk(_reader):
        raise OSError("the disk is full")

    store.copy_from = full_disk
    observer, _ = runtime(store)
    layer_status = replace(installed(), pj64_dir=str(project64))
    observed = observer(layer_status)
    assert observed["lab_roms"] is None and observed["rom"]["state"] == "supported"
    assert readiness(layer_status, observed)["step"] in ("verify", "ready")


def test_another_game_open_is_never_read_whole(tmp_path):
    vanilla = rom("vanilla")
    store = store_for(tmp_path)
    observer, reader = runtime(store, vanilla)
    observer(installed())
    assert reader.image_reads == 0 and store.rom_for("us") is None


# ---- the one row in the setup check -----------------------------------------------------------------------------


def held(tmp_path, *regions):
    store = store_for(tmp_path)
    for region in regions:
        store.adopt(rom(region))
    return store.held()


def check_with(tmp_path, lab_roms):
    return profile(fake_registry(), tmp_path).compare(layer(fake_folder(tmp_path)), US, lab_roms)


def test_both_versions_found_reads_same_and_flags_nothing(tmp_path):
    check = check_with(tmp_path, held(tmp_path, "us", "jp"))
    row = by_id(check)["lab_rom"]
    assert row["status"] == "match" and row["yours"] == "Found: v1.93u (US) and v1.93j (JP)"
    assert row["fix"] is None and check["flagged"] == []


@pytest.mark.parametrize("regions, yours", [((), "Not found"), (("jp",), "Only v1.93j (JP)")])
def test_no_us_rom_flags_the_row_with_the_stores_own_sentence(tmp_path, regions, yours):
    check = check_with(tmp_path, held(tmp_path, *regions))
    row = by_id(check)["lab_rom"]
    assert row["status"] == "differs" and row["yours"] == yours and row["importance"] == "recommended"
    assert row["fix"] == roms.missing_sentence("us") and check["flagged"] == ["lab_rom"]
    assert row["link"].startswith("https://wermi.neocities.org/emuguide/")


def test_without_a_store_there_is_no_row(tmp_path):
    check = check_with(tmp_path, None)
    assert "lab_rom" not in by_id(check) and len(check["items"]) == 10


def test_the_route_carries_setups_reading_into_the_check(tmp_path):
    folder = layer(fake_folder(tmp_path))
    status = SimpleNamespace(as_dict=lambda: folder)
    observed = {"rom": US, "lab_roms": held(tmp_path, "us")}
    check = setup_check(status, observed, profile(fake_registry(), tmp_path))
    assert by_id(check)["lab_rom"]["status"] == "match"
    assert "lab_rom" not in by_id(setup_check(status, {"rom": US}, profile(fake_registry(), tmp_path)))


def test_the_background_follower_copies_the_game_and_stops(tmp_path):
    game = FakeProject64(rom("us"))
    store = store_for(tmp_path, game=game, clock=lambda: 0.0)
    stop = roms.follow_game(store, interval_s=0.01)
    try:
        deadline = time.monotonic() + 5
        while store.rom_for("us") is None:
            assert time.monotonic() < deadline, "the follower never copied the game"
            time.sleep(0.01)
    finally:
        stop()
    assert store.rom_for("us").read_bytes() == rom("us") and game.image_reads == 1

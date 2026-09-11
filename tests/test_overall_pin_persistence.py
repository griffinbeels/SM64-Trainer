"""A failed Overall pin/reset must leave disk and every reading unchanged."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
import json
from pathlib import Path

import pytest

from sm64_events.library import assignment_transaction as persistence
from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.ranks import curves, scoring
from sm64_events.ranks.calibration import CalibrationRegistry, build_calibration
from sm64_events.ranks.standards import RankStandards

ENTITY = "star:2:4"
OTHER = "segment:99"
LADDER = {"Mario": 10., "Grandmaster": 12., "Master": 14., "Diamond": 16.,
          "Platinum": 18., "Gold": 20., "Silver": 25., "Bronze": 30.}


@pytest.fixture(params=["generated", "legacy"])
def ranks(tmp_path, request):
    path = tmp_path / "ranks.json"
    path.write_text(json.dumps({"version": 1, "entities": {
        ENTITY: {"clock": "igt", "strategies": {"Standard": LADDER},
                 "jp_strategies": {"Standard": {"Gold": 19.}},
                 "user_videos": {"Standard": {"Gold": "personal-video"}},
                 "overall_overrides": {"us": {"Gold": 19.}, "jp": {"Gold": 18.}}},
        OTHER: {"clock": "rta", "strategies": {"Other": {"Mario": 50.}},
                "overall_overrides": {"us": {"Mario": 49.}}}}}), encoding="utf-8")
    store = RankStandards(path)
    store.load()
    if request.param == "generated":
        regional = {}
        for version, offset in (("us", 0), ("jp", -100)):
            nodes = [[600 + offset, 100.], *[
                [round(seconds * 100) + offset, scoring.SCORE_ANCHORS[rank]]
                for rank, seconds in LADDER.items()]]
            regional[version] = curves.compile_curve(nodes, {"source": "community", "version": version})
        store.calibrations = CalibrationRegistry()
        store.calibrations.publish(build_calibration(
            {"schema_version": SCHEMA_VERSION, "sheet_revision": "2026-09-08T00:00:00",
             "targets": []}, {}, {}, {ENTITY: regional}, {}, "test-policy"))
    return store


def state(ranks):
    """Use the public reads that must agree, warming both regional curves."""
    return {"data": ranks.to_json(), "revision": ranks.calibration_revision,
            "pins": {version: ranks.overall_overrides(ENTITY, version) for version in ("us", "jp")},
            "curves": {version: ranks.overall_curve(ENTITY, version) for version in ("us", "jp")}}


def change(ranks, action):
    if action == "pin":
        ranks.set_overall_threshold(ENTITY, "Mario", 9., "us")
    else:
        ranks.reset_overall(ENTITY, "us" if action == "reset_us" else None)


def fail_persistence(monkeypatch, ranks, boundary, observed):
    def fail():
        observed.append(state(ranks))
        raise OSError(f"injected {boundary} failure")

    if boundary == "mkdir":
        original = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if path == ranks.path.parent:
                fail()
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", mkdir)
    elif boundary == "write":
        original = persistence.tempfile.NamedTemporaryFile

        @contextmanager
        def temporary(*args, **kwargs):
            with original(*args, **kwargs) as output:
                class PartialWrite:
                    name = output.name

                    def write(self, data):
                        output.write(data[:len(data) // 2])
                        output.flush()
                        fail()

                yield PartialWrite()

        monkeypatch.setattr(persistence.tempfile, "NamedTemporaryFile", temporary)
    else:
        original = persistence.os.replace

        def replace(source, destination):
            if Path(destination) == ranks.path:
                assert Path(source).read_bytes(), "replacement must follow a real temporary write"
                fail()
            return original(source, destination)

        monkeypatch.setattr(persistence.os, "replace", replace)


@pytest.mark.parametrize("action", ["pin", "reset_us", "reset_all"])
@pytest.mark.parametrize("boundary", ["mkdir", "write", "replace"])
def test_failed_pin_or_reset_preserves_disk_live_state_cache_and_captured_read(
        ranks, monkeypatch, action, boundary):
    before = state(ranks)
    saved = ranks.path.read_bytes()
    files = set(ranks.path.parent.iterdir())
    cache = deepcopy(ranks._overall_cache)
    assert cache, "the regression needs a warm curve cache"
    observed = []
    with ranks.read_context():
        captured = copy_context()
        with monkeypatch.context() as patch:
            fail_persistence(patch, ranks, boundary, observed)
            with pytest.raises(OSError, match=f"injected {boundary}"):
                change(ranks, action)
        assert ranks.is_current_read
        assert state(ranks) == before
    assert observed == [before], "the candidate must not become visible during persistence"
    assert ranks._overall_cache == cache
    assert state(ranks) == before
    assert captured.run(state, ranks) == before
    assert ranks.path.read_bytes() == saved
    assert set(ranks.path.parent.iterdir()) == files, "failed temporary files must be removed"


@pytest.mark.parametrize("action", ["pin", "reset_us", "reset_all"])
def test_successful_pin_and_resets_publish_together_and_survive_reload(ranks, action):
    before = state(ranks)
    with ranks.read_context():
        captured = copy_context()
        change(ranks, action)
        assert not ranks.is_current_read
        assert state(ranks) == before
    after = state(ranks)
    assert after["revision"] != before["revision"]
    expected = {"pin": {"us": {"Gold": 19., "Mario": 9.}, "jp": {"Gold": 18.}},
                "reset_us": {"us": {}, "jp": {"Gold": 18.}},
                "reset_all": {"us": {}, "jp": {}}}[action]
    assert after["pins"] == expected
    assert after["curves"]["us"]["ladder_cs"]["Gold"] == (1900 if action == "pin" else 2000)
    assert after["curves"]["us"]["ladder_cs"]["Mario"] == (900 if action == "pin" else 1000)
    assert after["curves"]["jp"]["ladder_cs"]["Gold"] == (1900 if action == "reset_all" else 1800)
    expected_data = deepcopy(before["data"])
    entity = expected_data["entities"][ENTITY]
    if action == "pin":
        entity["overall_overrides"]["us"]["Mario"] = 9.
    elif action == "reset_us":
        entity["overall_overrides"].pop("us")
    else:
        entity.pop("overall_overrides")
    assert after["data"] == expected_data, "Strategy standards and other entities must stay intact"
    assert json.loads(ranks.path.read_text(encoding="utf-8")) == after["data"]
    assert captured.run(state, ranks) == before
    reopened = RankStandards(ranks.path)
    reopened.load()
    reopened.calibrations = ranks.calibrations
    assert state(reopened) == after


@pytest.mark.parametrize("ranks", ["legacy"], indirect=True)
def test_a_captured_read_cannot_validate_a_new_pin_against_superseded_pins(ranks):
    before = state(ranks)
    with ranks.read_context():
        ranks.set_overall_threshold(ENTITY, "Gold", 18.5)
        saved = ranks.path.read_bytes()
        # 18.70 fits before the captured Gold=19.00 but crosses live Gold=18.50.
        with pytest.raises(ValueError, match="cross"):
            ranks.set_overall_threshold(ENTITY, "Platinum", 18.7)
        assert ranks.path.read_bytes() == saved
        assert state(ranks) == before
    assert ranks.overall_overrides(ENTITY, "us") == {"Gold": 18.5}
    assert ranks.overall_curve(ENTITY, "us")["ladder_cs"]["Gold"] == 1850


@pytest.mark.parametrize("action", ["pin", "reset_all"])
def test_failed_first_write_does_not_create_a_phantom_entity(tmp_path, monkeypatch, action):
    ranks = RankStandards(tmp_path / "missing" / "ranks.json")
    ranks.load()
    before = state(ranks)
    observed = []
    fail_persistence(monkeypatch, ranks, "mkdir", observed)
    with pytest.raises(OSError, match="injected mkdir"):
        change(ranks, action)
    assert observed == [before]
    assert state(ranks) == before
    assert not ranks.path.exists()


@pytest.mark.parametrize("ranks", ["generated"], indirect=True)
def test_read_capture_does_not_wait_for_a_pending_community_fit(ranks):
    before = state(ranks)

    def read():
        with ranks.read_context():
            return state(ranks)

    with ThreadPoolExecutor(max_workers=1) as worker:
        # A Sheet fit holds this lock until its completed generation publishes.
        with ranks.calibrations.update_lock:
            assert worker.submit(read).result(timeout=5) == before


def test_a_reset_between_cache_lookup_and_return_keeps_the_captured_curve(ranks):
    before = state(ranks)

    class ClearingCache(dict):
        cleared = False

        def clear_after_lookup(self):
            if not self.cleared:
                self.cleared = True
                ranks.reset_overall(ENTITY, "us")

        def get(self, key, default=None):
            value = super().get(key, default)
            self.clear_after_lookup()
            return value

        def __contains__(self, key):
            exists = super().__contains__(key)
            self.clear_after_lookup()
            return exists

    cache = ClearingCache(ranks._overall_cache)
    ranks._overall_cache = cache
    with ranks.read_context():
        observed = ranks.overall_curve(ENTITY, "us")
        assert cache.cleared, "the reset must occur after a successful cache lookup"
        assert observed == before["curves"]["us"]
    assert ranks.overall_overrides(ENTITY, "us") == {}
    assert ranks.overall_curve(ENTITY, "us")["ladder_cs"]["Gold"] == 2000

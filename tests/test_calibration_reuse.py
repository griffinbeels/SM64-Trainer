"""The calibration memo the app-building fixtures share (calibration_reuse.py).

It may make a build cheaper and nothing else: every app still gets a
calibration of its own, and a different input is never answered from another
build's result."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import calibration_reuse
from import_fixture import make_client
from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.ranks.calibration import build_calibration


@pytest.fixture(autouse=True)
def _empty_memo():
    """Each case counts from an empty memo and leaves nothing behind."""
    calibration_reuse.forget()
    yield
    calibration_reuse.forget()


def _scribble(calibration):
    """Everything a careless test could do to its own app's calibration."""
    target = calibration.payload["targets"][0]
    target["label"] = "leaked"
    target["approaches"][0]["entries"][0]["time_cs"] = 1
    target["approaches"].pop()
    next(iter(calibration.layers.values()))["strategies"].clear()
    next(iter(next(iter(calibration.overall.values())).values()))["nodes"].clear()
    calibration.rows["leaked"] = "segment:1"


def _view(calibration):
    return deepcopy((calibration.revision, calibration.payload["targets"][:3],
                     calibration.layers, calibration.overall, calibration.rows))


def test_an_app_that_scribbles_on_its_calibration_cannot_reach_the_next(tmp_path):
    """Builds two and three are served from the memo. The first app changes
    its own payload and generated standards in place, so does the second, and
    each later build must still read the calibration the first one prepared."""
    with make_client(tmp_path / "first") as (client, _db, _svc):
        first = client.app.state.library.calibrations.active
        pristine = _view(first)
        _scribble(first)
    for build in ("second", "third"):
        with make_client(tmp_path / build) as (client, _db, _svc):
            served = client.app.state.library.calibrations.active
            assert _view(served) == pristine, f"the {build} app read another app's changes"
            _scribble(served)
    assert calibration_reuse.stats == {"hits": 2, "misses": 1}


def _arguments():
    payload = {"schema_version": SCHEMA_VERSION, "sheet_revision": "2026-09-08T12:00:00",
               "targets": [{"entity_key": "star:2:4", "approaches": [
                   {"name": "Standard", "entries": [{"runner": "r", "time_cs": 1200}]}]}]}
    definitions = [{"id": 1, "name": "Movement", "seed_key": "seg:one",
                    "created_utc": "2026-09-22T00:00:00Z"}]
    standards = SimpleNamespace(calibration_inputs=lambda: {"clocks": {"star:2:4": "igt"},
                                                            "sheet": {}})
    policy = SimpleNamespace(revision="policy-1")
    return [payload, {"row": "star:2:4"}, definitions, standards, policy]


MOVES = {
    "payload": lambda args: args[0]["targets"][0]["approaches"][0]["entries"][0].update(time_cs=1100),
    "assignments": lambda args: args[1].update(row="segment:1"),
    "definitions": lambda args: args[2][0].update(seed_key="seg:two"),
    "standards": lambda args: args.__setitem__(3, SimpleNamespace(
        calibration_inputs=lambda: {"clocks": {"star:2:4": "rta"}, "sheet": {}})),
    "policy": lambda args: args.__setitem__(4, SimpleNamespace(revision="policy-2")),
}


@pytest.mark.parametrize("moved", [None, "created_utc", *MOVES])
def test_a_preparation_is_reused_only_for_the_same_arguments(monkeypatch, moved):
    """Every argument `prepare` receives is part of the key, the payload by
    content; a definition's insertion time is the one field left out."""
    prepared = []

    def genuine(payload, assignments, definitions, standards, policy):
        prepared.append(policy.revision)
        # Stamped with which preparation made it, so a reuse is visible.
        return build_calibration(payload, assignments, {}, {},
                                 {"prepared": str(len(prepared))}, policy.revision)

    monkeypatch.setattr(calibration_reuse, "_GENUINE", genuine)
    first = calibration_reuse._reusing_prepare(*_arguments())
    again = _arguments()
    if moved == "created_utc":
        again[2][0]["created_utc"] = "2026-09-23T00:00:00Z"
    elif moved is not None:
        MOVES[moved](again)
    second = calibration_reuse._reusing_prepare(*again)
    reused = moved in (None, "created_utc")
    assert len(prepared) == (1 if reused else 2)
    assert second.identities == {"prepared": "1" if reused else "2"}
    assert second is not first and second.payload is not first.payload

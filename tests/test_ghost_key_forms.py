"""Templates, native targets and practice-card keys name the same star/segment."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.ghost.session import GhostSession
from sm64_events.inputs.service import entity_key_of
from sm64_events.server.ghost_api import create_ghost_router
from sm64_events.storage.db import Database

from tests.ghost_fixture import FakeReplay, a_document
from sm64_events.ghost.native_session import NativeGhost

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

ATTEMPT_ID = 7


class FakeTracker:
    def __init__(self, db, target, strat_tag=None):
        self.db = db
        self.target = target
        self.strat_tag = strat_tag


class FakeBroadcaster:
    async def publish(self, event):
        return 1


def run_node(module: str, names: str, body: str):
    source = (UI / module).as_uri()
    script = (f"import {{ {names} }} from {source!r};\n"
              f"console.log(JSON.stringify({body}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def a_star_world(tmp_path):
    """A star the player is practising, marked and recorded. Returns
    (db, tracker_target, template_key, card_section)."""
    db = Database(tmp_path / "t.db")
    # The key is the INPUT layer's own answer for this attempt, never typed.
    kind, key = entity_key_of(SimpleNamespace(segment_id=None, course_id=24,
                                              star_id=1))
    db.input_templates.save(kind=kind, entity_key=key, strat_tag="10 coin",
                            name="the good one", origin=f"attempt:{ATTEMPT_ID}",
                            document=a_document())
    section = {"course_id": 24, "star_id": 1}
    return db, ("star", 24, 1), (kind, key), section


def a_segment_world(tmp_path):
    db = Database(tmp_path / "t.db")
    kind, key = entity_key_of(SimpleNamespace(segment_id=31, course_id=None,
                                              star_id=None))
    db.input_templates.save(kind=kind, entity_key=key, strat_tag=None,
                            name="the good one", origin=f"attempt:{ATTEMPT_ID}",
                            document=a_document())
    section = {"kind": "segment", "segment_id": 31}
    return db, ("segment", 31), (kind, key), section


def native_session_state(db, target, strat):
    """The real session and router resolve the template's saved native poses."""
    replay = FakeReplay(saved=[ATTEMPT_ID])
    session = GhostSession(tracker=FakeTracker(db, target, strat),
                           inputs=SimpleNamespace(templates=db.input_templates),
                           replay=replay, broadcaster=FakeBroadcaster(), native=NativeGhost(replay))
    app = FastAPI()
    app.include_router(create_ghost_router(session=session))
    with TestClient(app) as client:
        body = client.get("/api/ghost").json()
    assert body["source"]["available"], body
    return body


def walk(world, strat):
    db, target, (kind, key), section = world
    body = native_session_state(db, target, strat)
    assert body["target"]["entity_key"] == key
    assert body["source"]["template_id"] == db.input_templates.active_for(kind, key, strat).id
    card_key = run_node("entitysection.js", "entityKey",
                        f"entityKey({json.dumps(section)})")
    switch_key = run_node("ghostsession.js", "ghostTargetKey",
                          f"ghostTargetKey({json.dumps(body['target'])})")
    assert switch_key == card_key, (switch_key, card_key)
    return key, card_key


def test_a_star_resolves_the_same_template_at_every_hop(tmp_path):
    key, card_key = walk(a_star_world(tmp_path), "10 coin")
    # Named for the record: the two spellings really are different, so the
    # agreement above is a translation working rather than a tautology.
    assert key == "24-1" and card_key == "star:24:1"


def test_a_segment_resolves_the_same_template_at_every_hop(tmp_path):
    key, card_key = walk(a_segment_world(tmp_path), None)
    assert key == "31" and card_key == "segment:31"


def test_the_two_spellings_are_not_the_same_string(tmp_path):
    """The guard above would pass trivially if a card and a template happened
    to name a star identically — so this is the calibration that says they do
    not, and that the translation is doing real work."""
    _kind, key = entity_key_of(SimpleNamespace(segment_id=None, course_id=24,
                                               star_id=1))
    card_key = run_node("entitysection.js", "entityKey",
                        'entityKey({"course_id": 24, "star_id": 1})')
    assert key != card_key

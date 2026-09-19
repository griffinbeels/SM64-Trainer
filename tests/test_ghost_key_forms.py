"""ONE STAR, THREE SPELLINGS: do all of them reach the same template?

A star is named three different ways along the ghost's own path, and every one
of them is somebody's correct answer:

  * a TEMPLATE is filed as `("star", "24-1")` — `inputs/service.py::entity_key_of`
    owns that spelling, and the ghost hangs off a template;
  * a PRACTICE CARD calls the same star `star:24:1` — `ui/entitysection.js::entityKey`
    owns that one, and the switch lives on that card;
  * the OVERLAY PAGE is handed whatever the session put in its URL, and asks
    `GET /api/ghost/source` with it.

Each hop is written and tested on its own, and each is right on its own. What
nothing else checks is that the chain closes — so this file walks the real one:
the real template store, the real session, the real route, and the real
JavaScript, with every key DERIVED rather than typed in. A hand-typed key would
agree with itself whichever way the code spells things, which is exactly the
bug this is looking for.

Segments walk the same three hops (rule 11 parity), and their spelling is the
one that differs: a segment's template key is the bare id, while its card key
carries the `segment:` prefix.
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.ghost.session import GhostSession
from sm64_events.inputs.service import InputsService, entity_key_of
from sm64_events.server.ghost_api import create_ghost_router
from sm64_events.storage.db import Database

from tests.ghost_fixture import FakeReplay, a_document, a_view

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

ATTEMPT_ID = 7
PORT = 8099


class FakeTracker:
    def __init__(self, db, target, strat_tag=None):
        self.db = db
        self.target = target
        self.strat_tag = strat_tag


class FakeController:
    """Enough of the window for the session to point it somewhere."""

    def __init__(self, *, url, opacity=0.5, visible=False, **_kw):
        self.url = url
        self.opacity = opacity
        self.visible = bool(visible)
        self.alive = False

    def start(self):
        self.alive = True

    def stop(self):
        self.alive = False

    def is_running(self):
        return self.alive

    def set_url(self, url):
        self.url = url

    def set_opacity(self, value):
        self.opacity = float(value)

    def set_visible(self, value):
        self.visible = bool(value)


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


def overlay_url(db, target, strat) -> str:
    """What the session actually points the overlay window at."""
    replay = FakeReplay(saved=[ATTEMPT_ID], views={ATTEMPT_ID: a_view(ATTEMPT_ID)})
    made = []

    def factory(**kwargs):
        made.append(FakeController(**kwargs))
        return made[-1]

    session = GhostSession(tracker=FakeTracker(db, target, strat),
                           inputs=SimpleNamespace(templates=db.input_templates),
                           replay=replay, broadcaster=FakeBroadcaster(),
                           port=PORT, controller_factory=factory)
    body = asyncio.run(session.update(enabled=True))
    assert body["source"]["available"] is True, body
    assert made, "the session never opened an overlay"
    return made[0].url, body


def asked_by_the_page(db, url: str):
    """Replay the overlay page's own request: whatever is in its URL goes
    straight into `GET /api/ghost/source`."""
    query = {key: value[0] for key, value in
             parse_qs(urlsplit(url).query).items()}
    assert set(query) <= {"kind", "entity_key", "strat"}, query
    replay = FakeReplay(saved=[ATTEMPT_ID], views={ATTEMPT_ID: a_view(ATTEMPT_ID)})
    inputs = InputsService(db.inputs, db.input_templates, lambda: [])
    app = FastAPI()
    app.include_router(create_ghost_router(inputs, replay))
    with TestClient(app) as client:
        return client.get("/api/ghost/source", params=query)


def walk(world, strat):
    """One star or segment, through all three hops."""
    db, target, (kind, key), section = world
    url, body = overlay_url(db, target, strat)

    # HOP 1 — what the session filed the target under, and put in the URL.
    assert body["target"]["entity_key"] == key
    assert f"entity_key={key}" in url.replace("%2D", "-")

    # HOP 2 — the page asks with exactly that, and the route finds the template.
    answer = asked_by_the_page(db, url)
    assert answer.status_code == 200, answer.text
    assert answer.json()["available"] is True, answer.json()
    assert answer.json()["template_id"] == body["source"]["template_id"]

    # HOP 3 — the practice card's own key for the same thing, from the real
    # module that owns it, against the switch's translation of the session's.
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

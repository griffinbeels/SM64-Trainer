"""The app's desired roster, PB settle window and bounded admission lifecycle."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.ghost import rivals as module
from sm64_events.ghost.rival_recording import PopulationRecorder
from sm64_events.ghost.rivals import Rivals, wire
from sm64_events.ghost.pb import AttemptGhost
from sm64_events.ghost.adaptive import PracticeIdentity
from sm64_events.server.ghost_api import create_ghost_router
from sm64_events.storage.db import Database
from tests.test_ghost_adaptive import Standards, recording, basis, attempt

TARGET = {"kind":"star","entity_key":"2-1","strat":"Standard"}
CONTROLS = {"hue_degrees":250}


class Population:
    generation = 1

    def __init__(self):
        self.rows = [recording(f"{number:064x}",frames=600+number*20) for number in range(1,10)]

    def metadata_snapshot(self, *_args, **_kwargs):
        return self.rows

    def get(self, identity):
        found = next((row for row in self.rows if row["id"]==identity),None)
        if found is None:
            raise LookupError("Missing recording")
        return found

    def search(self, _identity, _player, *, cursor=None, limit=20):
        return {"records":self.rows[:limit],"next_cursor":cursor}


class Native:
    def __init__(self):
        self.preparations, self.canceled = [],0

    def set_portable(self, _provider):
        pass

    def prepare(self, target, ghosts, controls, generation):
        self.preparations.append((target,ghosts,controls,generation))
        return {"available":True,"ghosts":[]}

    def state(self):
        return {"active_generation":0}

    def cancel_preparation(self):
        self.canceled += 1


def controller(tmp_path, monkeypatch):
    tracker = SimpleNamespace(db=Database(tmp_path/"practice.db"),ranks=Standards())
    native, population, clock = Native(),Population(),[0.0]
    monkeypatch.setattr(module,"saved_pb_basis",lambda *_args:basis(step=10,frames=1200))
    rivals = Rivals(tracker,population,native,lambda:"us",now=lambda:clock[0])
    rivals.options.update(mode="fixed",min_step=0,max_step=44)
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    # The first refresh loads defaults from the fresh database.
    rivals.options.update(mode="fixed",min_step=0,max_step=44)
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    return rivals,tracker,native,population,clock


def test_forced_reads_do_not_reprepare_identical_rosters(tmp_path,monkeypatch):
    rivals,_,native,_,_ = controller(tmp_path,monkeypatch)
    before = len(native.preparations),rivals.generation
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert (len(native.preparations),rivals.generation)==before


def test_pb_undo_cancels_pending_and_survives_target_and_strategy_switch(tmp_path,monkeypatch):
    rivals,_,native,_,clock = controller(tmp_path,monkeypatch)
    rivals.pb_changed()
    assert native.canceled==1
    before = len(native.preparations)
    rivals.refresh({**TARGET,"strat":"Different"},CONTROLS,enabled=True,force=True)
    rivals.refresh({**TARGET,"entity_key":"2-2"},CONTROLS,enabled=True,force=True)
    after_other = len(native.preparations)
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert after_other==before+1 and len(native.preparations)==after_other
    monkeypatch.setattr(module,"saved_pb_basis",lambda *_args:basis(step=9,frames=1300))
    clock[0]=5.1
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    assert rivals.pb["frames"]==1300 and rivals.pb_deadline is None


def test_hidden_duplicate_pin_refused_without_mutation(tmp_path,monkeypatch):
    rivals,_,_,population,_ = controller(tmp_path,monkeypatch)
    identity = population.rows[-1]["id"]
    rivals.pins[rivals._key(TARGET)] = {"3":identity}
    rivals.options["count"]=1
    rivals.rows=()
    before = json.dumps(rivals.pins)
    with pytest.raises(ValueError,match="hidden"):
        rivals.checked({"action":"add","id":identity},TARGET)
    assert json.dumps(rivals.pins)==before


@pytest.mark.parametrize("identity", [None,{},[],17,"short","G"*64])
def test_malformed_rival_identity_is_a_checked_refusal(tmp_path,monkeypatch,identity):
    rivals,_,_,_,_ = controller(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match="identity"):
        rivals.checked({"action":"add","id":identity},TARGET)


def test_historical_pb_reserves_a_visible_slot_and_overflow_does_not_mutate_choices(tmp_path,monkeypatch):
    rivals,_,_,_,_ = controller(tmp_path,monkeypatch)
    monkeypatch.setattr(module,"saved_pb_basis",lambda *_args:None)
    assert rivals.library_limit(TARGET)==4
    ghosts = [AttemptGhost(1,"star","2-1","Standard",600,pb=True),
              *(AttemptGhost(number,"star","2-1","Standard",600) for number in range(2,6))]
    before = json.dumps(rivals.pins),dict(rivals.local_choices)
    with pytest.raises(ValueError,match="historical"):
        rivals.choose_library(TARGET,ghosts,True)
    assert (json.dumps(rivals.pins),rivals.local_choices)==before


def test_new_database_and_capture_source_force_new_preparation(tmp_path,monkeypatch):
    rivals,tracker,native,_,_ = controller(tmp_path,monkeypatch)
    before=rivals.generation
    tracker.db=Database(tmp_path/"fresh.db")
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    assert rivals.generation>before
    before=rivals.generation
    rivals.source_of=lambda:"new-capture"
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    assert rivals.generation==before+1 and native.preparations[-1][-1]==rivals.generation


def test_overlay_off_does_not_prepare_clip_bytes(tmp_path,monkeypatch):
    rivals,_,native,_,_ = controller(tmp_path,monkeypatch)
    count=len(native.preparations)
    rivals.refresh(TARGET,CONTROLS,enabled=False)
    assert len(native.preparations)==count
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    assert len(native.preparations)==count+1


def test_wire_omits_private_playback_and_local_lineage():
    assert wire({"id":"one","source":{"attempt_id":1},"ghost":object(),"path":"private","manifest":{}})=={"id":"one"}


def test_settling_a_different_star_never_reuses_previous_available_source(tmp_path,monkeypatch):
    rivals,_,_,_,_ = controller(tmp_path,monkeypatch)
    other={**TARGET,"entity_key":"2-2"}
    rivals.pb_changed(other)
    answer=rivals.refresh(other,CONTROLS,enabled=True)
    assert answer["available"] is False and answer["ghosts"]==[]


def test_private_local_choices_restore_and_reset_without_certification(tmp_path,monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    tracker.db.upsert_attempt(attempt())
    rivals.choose_library(TARGET,[AttemptGhost(1,"star","2-1","Standard",600)],True)
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    chosen=next(row for row in rivals.rows if row["pinned"])
    assert chosen["manual_only"] and "certified" not in chosen
    restored=Rivals(tracker,population,native,lambda:"us")
    restored.refresh(TARGET,CONTROLS,enabled=True)
    assert restored.rows[0]["id"]==chosen["id"] and restored.rows[0]["pinned"]
    restored.apply(restored.checked({"action":"reset","slot":0},TARGET))
    restored.refresh(TARGET,CONTROLS,enabled=True)
    assert not any(row["pinned"] for row in restored.rows)


def test_slower_same_division_finish_skips_preservation(tmp_path):
    tracker=SimpleNamespace(db=Database(tmp_path/"practice.db"),ranks=Standards())
    tracker.db.set_state("ghost_player_id","known-player")
    population=Population()
    population.rows=[recording("a"*64,frames=699,player_id="known-player")]
    identity=PracticeIdentity("star","2-1","igt","us","Standard")
    recorder=PopulationRecorder(population,object(),tracker,lambda _attempt:identity)
    # This provider intentionally has no preservation/admission method.
    assert recorder._admit(attempt(frames=700)) is False


def test_saved_run_joins_without_replacing_other_pins_and_unsave_clears_only_its_lineage(tmp_path,monkeypatch):
    rivals,tracker,_,population,_ = controller(tmp_path,monkeypatch)
    run = attempt()
    tracker.db.upsert_attempt(run)
    protected = population.rows[-1]["id"]
    rivals.pins[rivals._key(TARGET)] = {"3":protected}
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert rivals.remember_saved(TARGET,run)
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    choices = rivals.pins[rivals._key(TARGET)]
    local = next(identity for identity in choices.values() if identity != protected)
    assert protected in choices.values() and local in rivals.local_choices
    assert rivals.remember_saved(TARGET,run) and list(choices.values()).count(local)==1
    rivals.forget_saved(run)
    assert rivals.pins[rivals._key(TARGET)]=={"3":protected} and local not in rivals.local_choices


def test_saving_with_all_visible_pins_keeps_them_and_does_not_leak_a_local_reference(tmp_path,monkeypatch):
    rivals,_,_,population,_ = controller(tmp_path,monkeypatch)
    rivals.pins[rivals._key(TARGET)] = {str(slot):population.rows[slot]["id"] for slot in range(4)}
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    before = json.dumps(rivals.pins),dict(rivals.local_choices)
    assert not rivals.remember_saved(TARGET,attempt())
    assert (json.dumps(rivals.pins),rivals.local_choices)==before


@pytest.mark.parametrize("occupied", [1,4])
def test_saved_run_respects_persisted_pins_while_strategy_change_is_settling(tmp_path,monkeypatch,occupied):
    rivals,tracker,_,population,_ = controller(tmp_path,monkeypatch)
    protected = {str(slot):population.rows[slot]["id"] for slot in range(occupied)}
    rivals.pins[rivals._key(TARGET)] = dict(protected)
    rivals.pb_changed(TARGET)
    target = {**TARGET,"strat":"Different"}
    rivals.refresh(target,CONTROLS,enabled=True)
    assert not rivals.rows and rivals.pb_deadline is not None
    run = attempt(strat_tag="Different")
    tracker.db.upsert_attempt(run)
    assert rivals.remember_saved(target,run)
    choices = rivals.pins[rivals._key(target)]
    assert len(choices)==1
    assert rivals.pins[rivals._key(TARGET)]==protected


def test_reopening_preserves_referenced_local_choices_across_many_targets(tmp_path,monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    references = {f"{number:064x}":{"attempt_id":number,"session_id":1,"started_utc":"2026-10-07T00:00:00Z"}
                  for number in range(516)}
    pins = {json.dumps(["segment",str(target)]):{str(slot):f"{target*4+slot:064x}" for slot in range(4)}
            for target in range(129)}
    tracker.db.set_state(module.STATE_KEY,{"version":1,"options":rivals.options,"pins":pins,
                                          "local_choices":{**references,"unused":{}}})
    restored = Rivals(tracker,population,native,lambda:"us")
    restored._load()
    assert restored.local_choices==references and restored.pins==pins


def test_malformed_preferences_in_new_profile_do_not_retain_previous_profiles_pins(tmp_path,monkeypatch):
    rivals,tracker,_,population,_ = controller(tmp_path,monkeypatch)
    rivals.pins[rivals._key(TARGET)] = {"0":population.rows[0]["id"]}
    tracker.db = Database(tmp_path/"other.db")
    tracker.db.set_state(module.STATE_KEY,{"options":{},"pins":[]})
    rivals._load()
    assert not rivals.pins and not rivals.local_choices


def test_queue_never_reads_identity_and_stop_joins_preservation(tmp_path):
    entered, release = threading.Event(),threading.Event()
    threads=[]
    tracker=SimpleNamespace(db=Database(tmp_path/"practice.db"))
    def identity_of(_attempt):
        threads.append(threading.get_ident())
        return SimpleNamespace(clock="igt",game_version="us")
    class Admission:
        def accept_verified(self,*_args,**_kwargs):
            entered.set()
            assert release.wait(3)
    recorder=PopulationRecorder(Admission(),object(),tracker,identity_of)
    async def check():
        loop_thread=threading.get_ident()
        recorder.start()
        recorder.enqueue(attempt())
        assert not threads
        await asyncio.to_thread(entered.wait,3)
        stop=asyncio.create_task(recorder.stop())
        await asyncio.sleep(.03)
        assert not stop.done() and threads[0]!=loop_thread
        release.set()
        await stop
        assert recorder.job is None
    asyncio.run(check())


def test_search_route_decodes_keyset_cursor_and_rejects_malformed_json():
    class Session:
        async def search_rivals(self,target,player,cursor):
            assert target==TARGET and player=="Form" and cursor==["formframe",600,"a"*64]
            return {"records":[],"next_cursor":None}
    app=FastAPI()
    app.include_router(create_ghost_router(Session()))
    with TestClient(app) as client:
        params={"target":json.dumps(TARGET),"player":"Form","cursor":json.dumps(["formframe",600,"a"*64])}
        assert client.get("/api/ghost/rivals/search",params=params).status_code==200
        assert client.get("/api/ghost/rivals/search",params={**params,"cursor":"broken"}).status_code==422


@pytest.mark.parametrize("choice,capacity", [
    ({"pb":False,"attempts":[1,2,3,4,5]},5),
    ({"pb":True,"attempts":[1,2,3,4]},4),
])
def test_library_refuses_overflow_before_preservation(choice,capacity):
    class Session:
        _rivals=SimpleNamespace(library_limit=lambda _target:capacity)
        def _read_target(self):
            return TARGET
        def _sharing(self):
            raise AssertionError("No files may be preserved for an excessive roster")
    app=FastAPI()
    app.include_router(create_ghost_router(Session()))
    with TestClient(app) as client:
        response=client.post("/api/ghost/library/prepare-race",json={"target":TARGET,
            "ghosts":choice})
        assert response.status_code==422 and "four" in response.text.replace("4","four")

"""The app's desired roster, immediate PB changes and bounded admission lifecycle."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.ghost import rivals as module
from sm64_events.ghost.rival_recording import PopulationRecorder
from sm64_events.ghost.bundled_beta import AUTHOR, PLAYER_ID, BundledPopulation
from sm64_events.ghost.library_preferences import save_author
from sm64_events.ghost.rivals import Rivals, wire
from sm64_events.ghost.pb import AttemptGhost
from sm64_events.ghost.adaptive import PracticeIdentity
from sm64_events.server.ghost_api import create_ghost_router
from sm64_events.storage.db import Database
from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.tracking.service import TrackerService
from sm64_events.ghost.adaptive import rank_frames
from datetime import datetime, timezone
from tests.test_ghost_adaptive import Standards, SharedStandards, recording, basis, attempt

TARGET = {"kind":"star","entity_key":"2-1","strat":"Standard"}
CONTROLS = {"hue_degrees":250}


def test_scoped_clear_then_delete_previous_session_refreshes_real_pb_and_roster(tmp_path):
    """Actual journal wipes preserve prior PBs, then reset the adaptive baseline."""
    db = Database(tmp_path / "reset.db")
    service = TrackerService(db, Broadcaster(), Standards())
    service.observed_game_version = lambda: "us"
    population, native = Population(), Native()
    population.rows = []
    identity = PracticeIdentity("star", "2-1", "igt", "us", "Standard")
    for frames in range(500, 1200, 10):
        progress = rank_frames(service.ranks, identity, frames)
        if progress and progress["step"] > 5:
            population.rows.append(recording(f"{frames:064x}", frames=frames))
    rivals = Rivals(service, population, native, lambda: "us")
    snapshots = []

    async def changed(_attempt, _saved):
        rivals.pb_changed()
        rivals.refresh(TARGET, CONTROLS, enabled=True)
        snapshots.append((rivals.pb, tuple(row["id"] for row in rivals.rows)))

    async def finish(frame, frames):
        now = datetime.now(timezone.utc)
        await service.publish(Event(type="practice_reset", frame=frame, timestamp_utc=now,
                                    payload={"igt_frames_before": 0}))
        await service.publish(Event(type="star_collected", frame=frame+frames, timestamp_utc=now,
                                    payload={"course_id":2, "star_id":1, "igt_frames":frames,
                                             "igt_timed_at":"xcam"}))
        played = next(row for row in db.attempts() if row.igt_frames == frames)
        await service.save_pb(played.id, "igt")
        return played

    async def scenario():
        await service.start()
        await service.set_target(2, 1, "Standard")
        old = await finish(100, 1200)
        await service.new_session()
        await finish(2000, 900)
        service.on_pb_changed = changed
        rivals.refresh(TARGET, CONTROLS, enabled=True)
        assert rivals.pb["frames"] == 900 and rivals.rows
        before = tuple(row["id"] for row in rivals.rows)
        await service.wipe_data("all", scope="session")
        assert rivals.pb["frames"] == 1200 and rivals.rows
        assert tuple(row["id"] for row in rivals.rows) != before
        assert [row.id for row in db.attempts()] == [old.id]
        await service.delete_session(old.session_id)
        assert rivals.pb is None and len(rivals.rows) == 4
        # No PB spreads across the two lowest tiers; seats already there keep their slots.
        steps = {row["frames"]: rank_frames(service.ranks, identity, row["frames"])["step"] for row in rivals.rows}
        assert all(step <= 9 for step in steps.values()) and len({row["player_id"] for row in rivals.rows}) == 4
        assert len(native.preparations[-1][1]) == 4
        assert len(snapshots) == 2 and population.rows

    try:
        asyncio.run(scenario())
    finally:
        db.close()


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

    def keep(self, _identifiers):
        pass


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

    def reconcile(self, **_command):
        pass


def controller(tmp_path, monkeypatch):
    tracker = SimpleNamespace(db=Database(tmp_path/"practice.db"),ranks=Standards())
    native, population, clock = Native(),Population(),[0.0]
    monkeypatch.setattr(module,"saved_pb_basis",lambda *_args:basis(step=10,frames=1200))
    rivals = Rivals(tracker,population,native,lambda:"us")
    rivals.options.update(mode="fixed",min_step=0,max_step=44)
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    # The first refresh loads defaults from the fresh database.
    rivals.options.update(mode="fixed",min_step=0,max_step=44)
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    return rivals,tracker,native,population,clock


@pytest.mark.parametrize("cloud_active", [False, True])
def test_complete_historical_bundle_falls_back_offline_and_remains_manually_selectable(tmp_path, monkeypatch, cloud_active):
    class Bundle(Population):
        def __init__(self):
            self.rows = [recording(f"{number:064x}", frames=600 + number * 20,
                source_status="bundled-author-recording", player_id="bundled:griffman1212",
                structurally_valid=True, certified=False, full_start=None, recorded_game_version=None)
                for number in range(1, 6)]

        def get(self, identifier):
            return {**super().get(identifier), "game_version": None}

        def coverage(self, _identity):
            return {"complete": False, "generation": None}

    tracker = SimpleNamespace(db=Database(tmp_path / "bundle.db"), ranks=SharedStandards())
    ghost = AttemptGhost(1, "star", "2-1", "Standard", 100, pb=True)
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_args: basis(step=44, frames=100, ghost=ghost))
    native, population = Native(), Bundle()
    sync = SimpleNamespace(account=SimpleNamespace(snapshot=lambda: {
        "authenticated": True, "status": "active", "uid": "11111111-1111-4111-8111-111111111111"}),
        last_error="network_unavailable", request_pool=lambda *_args, **_kwargs: None,
        pool_status=lambda _identity: "offline", refused_assets=frozenset) if cloud_active else None
    rivals = Rivals(tracker, population, native, lambda: "us", community=sync)
    try:
        rivals.refresh(TARGET, CONTROLS, enabled=True)
        # One author in the bundle seats one automatic rival (his rule, round 39:
        # "per rival group, there should be only unique players"); the rest stay
        # manually selectable below.
        assert [row["frames"] for row in rivals.rows] == [620]
        prepared = native.preparations[-1][1]
        assert len(prepared) == 2 and prepared[0].ghost is ghost
        last = population.rows[-1]
        checked = rivals.checked({"action": "add", "id": last["id"], "slot": 0}, TARGET)
        assert checked == {"action": "add", "id": last["id"], "slot": 0}
        assert population.get(last["id"])["game_version"] is None
    finally:
        tracker.db.close()


def test_forced_reads_do_not_reprepare_identical_rosters(tmp_path,monkeypatch):
    rivals,_,native,_,_ = controller(tmp_path,monkeypatch)
    before = len(native.preparations),rivals.generation
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert (len(native.preparations),rivals.generation)==before


def test_same_clip_and_colour_moving_slot_refreshes_native_name(tmp_path, monkeypatch):
    rivals, tracker, native, _, _ = controller(tmp_path, monkeypatch)
    try:
        before = len(native.preparations)
        chosen = [dict(row) for row in rivals.rows]
        assert chosen
        chosen[0]["slot"] = (chosen[0]["slot"] + 1) % 4
        monkeypatch.setattr(module, "select_rivals", lambda *_args, **_kwargs: chosen)
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        assert len(native.preparations) == before + 1
        ghosts = native.preparations[-1][1]
        assert ghosts[0].rival_slot == chosen[0]["slot"]
    finally:
        tracker.db.close()


@pytest.mark.parametrize("available", [True, False])
def test_unchanged_pb_refresh_retains_playback_and_reason(tmp_path, monkeypatch, available):
    rivals, _, native, _, _ = controller(tmp_path, monkeypatch)
    ghost = AttemptGhost(1, "star", "2-1", "Standard", 1200, pb=True)
    pb = basis(step=10, frames=1200, attempt_id=1, ghost=ghost)
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: dict(pb))
    reason = None if available else "This run has no saved Mario poses."

    def prepare(target, ghosts, controls, generation):
        native.preparations.append((target, ghosts, controls, generation))
        return {"available": available, "ghosts": [{"pb": True, "available": available, "reason_text": reason}]}

    monkeypatch.setattr(native, "prepare", prepare)
    rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
    before = len(native.preparations), rivals.generation
    rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
    assert (len(native.preparations), rivals.generation) == before
    assert rivals.payload()["pb"]["available"] is available
    assert rivals.payload()["pb"]["reason_text"] == reason


@pytest.mark.parametrize("case", ["ready", "already-ready", "missing", "superseded", "other-run", "new-database", "new-lineage"])
def test_pose_commit_retries_only_a_refused_still_current_local_pb(tmp_path, monkeypatch, case):
    rivals, tracker, native, _, _ = controller(tmp_path, monkeypatch)
    ghost = AttemptGhost(1, "star", "2-1", "Standard", 1200, pb=True)
    pb = basis(step=10, frames=1200, attempt_id=1, ghost=ghost, source={"session_id": 1})
    current = [pb]
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: current[0])
    rivals.pb = {**pb, "available": case == "already-ready"}
    probes = []

    def probe(target, saved):
        probes.append((target, saved))
        return {"available": case != "missing"}

    monkeypatch.setattr(native, "probe", probe, raising=False)
    if case == "superseded":
        current[0] = None
    elif case == "new-lineage":
        current[0] = {**pb, "source": {"session_id": 2}}
    elif case == "new-database":
        tracker.db = Database(tmp_path / "different.db")
    before = rivals.fingerprint, rivals.prepared_key, len(native.preparations), rivals.generation
    retried = rivals.pb_preserved(2 if case == "other-run" else 1)
    assert retried is (case == "ready")
    assert len(probes) == int(case in ("ready", "missing"))
    if retried:
        assert rivals.fingerprint is None and rivals.prepared_key is None
        assert (len(native.preparations), rivals.generation) == before[2:]
    else:
        assert (rivals.fingerprint, rivals.prepared_key, len(native.preparations), rivals.generation) == before


def test_pb_and_undo_recompute_immediately_without_repreparing_unchanged_clips(tmp_path,monkeypatch):
    rivals,_,native,_,clock = controller(tmp_path,monkeypatch)
    rivals.pb_changed()
    assert native.canceled==0
    before = len(native.preparations),rivals.generation
    monkeypatch.setattr(module,"saved_pb_basis",lambda *_args:basis(step=9,frames=1300))
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    assert rivals.pb["frames"]==1300 and clock[0]==0
    assert (len(native.preparations),rivals.generation)==before
    rivals.pb_changed()
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    assert (len(native.preparations),rivals.generation)==before
    rivals.refresh({**TARGET,"strat":"Different"},CONTROLS,enabled=True,force=True)
    rivals.refresh({**TARGET,"entity_key":"2-2"},CONTROLS,enabled=True,force=True)
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert rivals.pb["frames"]==1300 and len(native.preparations)>before[0]


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
    count,generation=len(native.preparations),rivals.generation
    rivals.refresh(TARGET,CONTROLS,enabled=False)
    assert len(native.preparations)==count
    assert rivals.payload()["status"] is None
    # Switched back on, the renderer still holds this roster and its race
    # resumes (his ask, 2026-10-17): nothing is prepared again.
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert (len(native.preparations),rivals.generation)==(count,generation)
    assert rivals.payload()["status"] is None
    # A renderer that no longer holds it (a new capture) prepares once on.
    rivals.source_of=lambda:"new-capture"
    rivals.refresh(TARGET,CONTROLS,enabled=False)
    assert len(native.preparations)==count
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    assert len(native.preparations)==count+1 and rivals.generation==generation+1


def test_wire_omits_private_playback_and_local_lineage():
    assert wire({"id":"one","source":{"attempt_id":1},"ghost":object(),"path":"private","manifest":{}})=={"id":"one"}


def test_pb_on_a_different_star_resolves_that_star_immediately(tmp_path,monkeypatch):
    rivals,_,_,_,_ = controller(tmp_path,monkeypatch)
    other={**TARGET,"entity_key":"2-2"}
    rivals.pb_changed(other)
    answer=rivals.refresh(other,CONTROLS,enabled=True)
    assert answer["available"] is False and answer["ghosts"]==[] and answer["reason"]=="no_rivals"


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
def test_saved_run_respects_persisted_pins_after_strategy_change(tmp_path,monkeypatch,occupied):
    rivals,tracker,_,population,_ = controller(tmp_path,monkeypatch)
    protected = {str(slot):population.rows[slot]["id"] for slot in range(occupied)}
    rivals.pins[rivals._key(TARGET)] = dict(protected)
    rivals.pb_changed(TARGET)
    target = {**TARGET,"strat":"Different"}
    rivals.refresh(target,CONTROLS,enabled=True)
    assert not rivals.rows and rivals.target==target
    run = attempt(strat_tag="Different")
    tracker.db.upsert_attempt(run)
    assert rivals.remember_saved(target,run)
    choices = rivals.pins[rivals._key(target)]
    assert len(choices)==1
    assert rivals.pins[rivals._key(TARGET)]==protected


def test_pending_requires_native_readiness_and_clears_on_adoption(tmp_path,monkeypatch):
    rivals,_,native,_,_ = controller(tmp_path,monkeypatch)
    assert rivals.payload()["status"] is None
    native.state=lambda:{"prepared_generation":rivals.generation,"active_generation":0}
    assert rivals.payload()["status"]=="Updates on your next attempt."
    native.state=lambda:{"prepared_generation":rivals.generation,"active_generation":rivals.generation}
    assert rivals.payload()["status"] is None
    before=rivals.generation,len(native.preparations)
    rivals.pb_changed()
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    assert (rivals.generation,len(native.preparations))==before and rivals.payload()["status"] is None


def test_automatic_rivals_exclude_own_runs_but_preserve_explicit_choices(tmp_path,monkeypatch):
    rivals,tracker,_,population,_=controller(tmp_path,monkeypatch)
    tracker.db.set_state("ghost_player_id",population.rows[0]["player_id"])
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert all(row["player_id"]!=population.rows[0]["player_id"] for row in rivals.rows)
    rivals.pins[rivals._key(TARGET)]={"0":population.rows[0]["id"]}
    rivals.refresh(TARGET,CONTROLS,enabled=True,force=True)
    assert rivals.rows[0]["id"]==population.rows[0]["id"] and rivals.rows[0]["pinned"]


@pytest.mark.parametrize("simulated", [False, True])
@pytest.mark.parametrize("frames", [640, 600])
@pytest.mark.parametrize("count", [1, 9])
def test_fresh_profile_fills_from_below_like_normal_play(tmp_path,monkeypatch,simulated,frames,count):
    rivals,_,native,population,clock=controller(tmp_path,monkeypatch)
    population.rows=population.rows[:count]
    monkeypatch.setattr(rivals,"_simulation",lambda:{"zero_practice":True} if simulated else None)
    monkeypatch.setattr(module,"saved_pb_basis",lambda *_args:basis(step=44,frames=frames))
    rivals.options.update(mode="adaptive")
    rivals.pb_changed()
    before=rivals.generation
    rivals.refresh(TARGET,CONTROLS,enabled=True)
    assert clock[0]==0 and rivals.generation==before+1
    rows=rivals.payload()["rows"]
    assert len(rows)==min(4,count) and len({row["player_id"] for row in rows})==len(rows)
    # With no recording above the range, fill from the closest subdivisions
    # below, even after the tester beats every donated recording.
    expected=sorted(population.rows,key=lambda row:row["frames"])[:4]
    assert {row["id"] for row in rows}=={row["id"] for row in expected}
    assert native.preparations[-1][-1]==rivals.generation


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


# His rule (2026-10-13): "the player doesn't ever actually fight their own ghosts
# as a rival (this would read as an error to most people)". Only the PB ghost is
# theirs. The identities that count as "mine" are the local recorder id, the
# signed-in account, and the bundled collection when the player is its author.
UID = "11111111-1111-4111-8111-111111111111"


class CloudPopulation(Population):
    """The plain fake with the coverage answer the cloud wrapper gives."""

    def coverage(self, _identity):
        return {"complete": True, "generation": "g1"}


class BundledRows(CloudPopulation):
    """The bundled collection's rows under its real ownership rule, over no local recordings."""
    owned_player_ids = BundledPopulation.owned_player_ids

    def __init__(self):
        self.local = SimpleNamespace()
        self.rows = [recording(f"{number:064x}", frames=600 + number * 20, player_id=PLAYER_ID, player_name=AUTHOR,
                               source_status="bundled-author-recording", structurally_valid=True, certified=False,
                               full_start=None, recorded_game_version=None) for number in range(1, 6)]


def signed_in(name, uid=UID):
    return SimpleNamespace(account=SimpleNamespace(snapshot=lambda: {
        "authenticated": True, "status": "active", "uid": uid, "name": name}),
        last_error=None, request_pool=lambda *_args, **_kwargs: None, request_assets=lambda *_args, **_kwargs: None,
        pool_status=lambda _identity: None, refused_assets=frozenset)


def seated(tmp_path, monkeypatch, population, community=None, *, author=None):
    tracker = SimpleNamespace(db=Database(tmp_path / "own.db"), ranks=SharedStandards())
    if author:
        save_author(tracker.db, author)
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_args: basis(step=10, frames=1200))
    rivals = Rivals(tracker, population, Native(), lambda: "us", community=community)
    rivals.refresh(TARGET, CONTROLS, enabled=True)
    rivals.options.update(mode="fixed", min_step=0, max_step=44)
    rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
    return rivals, tracker


def test_the_collection_author_never_races_their_own_bundled_recordings_unless_pinned(tmp_path, monkeypatch):
    population = BundledRows()
    rivals, tracker = seated(tmp_path, monkeypatch, population, signed_in("GriffMan1212"))
    try:
        assert rivals.rows == ()
        last = population.rows[-1]
        rivals.pins[rivals._key(TARGET)] = {"0": last["id"]}
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        assert [(row["id"], row["player_id"], row["pinned"]) for row in rivals.rows] == [(last["id"], PLAYER_ID, True)]
    finally:
        tracker.db.close()


def test_another_signed_in_player_races_the_bundled_author(tmp_path, monkeypatch):
    population = BundledRows()
    rivals, tracker = seated(tmp_path, monkeypatch, population, signed_in("gtest"))
    try:
        # One automatic seat per player, holding the author's run nearest above the PB.
        assert [(row["frames"], row["player_id"]) for row in rivals.rows] == [(700, PLAYER_ID)]
    finally:
        tracker.db.close()


def test_the_export_credit_alone_identifies_the_bundled_author_offline(tmp_path, monkeypatch):
    population = BundledRows()
    rivals, tracker = seated(tmp_path, monkeypatch, population, None, author="Griffman1212")
    try:
        assert rivals.rows == ()
        assert rivals._owned_players() == {PLAYER_ID}
    finally:
        tracker.db.close()


def test_a_community_recording_under_the_signed_in_account_is_never_an_automatic_rival(tmp_path, monkeypatch):
    population = CloudPopulation()
    population.rows[0] = {**population.rows[0], "player_id": UID}
    rivals, tracker = seated(tmp_path, monkeypatch, population, signed_in("Runner"))
    try:
        assert rivals.rows and all(row["player_id"] != UID for row in rivals.rows)
        assert UID in rivals._owned_players()
    finally:
        tracker.db.close()


def test_add_rejects_a_second_recording_of_a_seated_player_but_allows_replacing_that_slot(tmp_path, monkeypatch):
    rivals, tracker, _, population, _ = controller(tmp_path, monkeypatch)
    try:
        first = rivals.rows[0]
        extra = {**population.rows[0], "id": "e" * 64, "player_id": first["player_id"]}
        population.rows.append(extra)
        with pytest.raises(ValueError, match="player is already"):
            rivals.checked({"action": "add", "id": extra["id"], "slot": 1}, TARGET)
        accepted = rivals.checked({"action": "add", "id": extra["id"], "slot": first["slot"]}, TARGET)
        assert accepted["slot"] == first["slot"]
        rivals.apply(accepted)
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        assert len([row for row in rivals.rows if row.get("player_id") == first["player_id"]]) == 1
    finally:
        tracker.db.close()


def test_known_local_and_account_self_ids_share_one_pin_without_aliasing_an_equal_name(tmp_path, monkeypatch):
    population = CloudPopulation()
    local, cloud, stranger, extra = population.rows[:4]
    population.rows = [{**local, "player_id": "local-recorder", "player_name": AUTHOR},
                      {**cloud, "player_id": UID, "player_name": AUTHOR},
                      {**stranger, "player_id": "different-account", "player_name": AUTHOR},
                      {**extra, "player_id": UID, "player_name": AUTHOR}]
    rivals, tracker = seated(tmp_path, monkeypatch, population, signed_in(AUTHOR))
    try:
        tracker.db.set_state("ghost_player_id", "local-recorder")
        rivals.pins[rivals._key(TARGET)] = {"0": local["id"], "1": cloud["id"]}
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        playable = [row for row in rivals.rows if row.get("ghost") is not None]
        assert {row["id"] for row in playable} == {local["id"], stranger["id"]}
        assert rivals.rows[1]["available"] is False
        assert all("_owned" not in wire(row) for row in rivals.rows)
        with pytest.raises(ValueError, match="player is already"):
            rivals.checked({"action": "add", "id": extra["id"], "slot": 2}, TARGET)
    finally:
        tracker.db.close()


def test_a_saved_local_attempt_shares_own_population_seat_but_private_credit_does_not(tmp_path, monkeypatch):
    from sm64_events.ghost.adaptive import player_key, select_rivals
    from sm64_events.ghost.pb import local_attempt_source
    rivals, tracker, _, population, _ = controller(tmp_path, monkeypatch)
    try:
        run = attempt(91, frames=700, game_version="us")
        monkeypatch.setattr(tracker.db, "attempt", lambda number: run if number == run.id else None)
        identity = rivals._remember_local(local_attempt_source(run))
        saved = rivals._private_record(identity, rivals.identity)
        assert saved is not None and saved["_owned"] is True
        tracker.db.set_state("ghost_player_id", population.rows[0]["player_id"])
        own = rivals._seat_row({**population.rows[0], "step": saved["step"]})
        assert player_key(saved) == player_key(own)
        assert player_key({**own, "_owned": False, "player_id": "private:you"}) != player_key(saved)
        result = select_rivals([saved, own], basis(), {}, {"0": saved["id"], "1": own["id"]})
        assert len([row for row in result if row.get("ghost") is not None]) == 1
    finally:
        tracker.db.close()


def test_account_change_recomputes_prior_rows_self_alias_before_download_staging(tmp_path, monkeypatch):
    population = CloudPopulation()
    population.rows[0] = {**population.rows[0], "player_id": UID}
    rivals, tracker = seated(tmp_path, monkeypatch, population, signed_in(AUTHOR))
    try:
        rivals.pins[rivals._key(TARGET)] = {"0": population.rows[0]["id"]}
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        assert rivals.rows[0]["_owned"] is True
        rivals.community.sync = signed_in("Other", "22222222-2222-4222-8222-222222222222")
        staged = []
        select = module.select_rivals
        def capture(rows, pb, options, pins, previous=(), **kwargs):
            staged.extend(previous)
            return select(rows, pb, options, pins, previous, **kwargs)
        monkeypatch.setattr(module, "select_rivals", capture)
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        assert staged[0]["player_id"] == UID and staged[0]["_owned"] is False
        assert rivals.rows[0]["_owned"] is False
    finally:
        tracker.db.close()


def test_own_seats_are_marked_by_identity_never_by_name(tmp_path, monkeypatch):
    """A rival who calls themselves "You" is still a rival; the player's own
    saved run is marked `own` from who recorded it."""
    rivals, tracker, _, population, _ = controller(tmp_path, monkeypatch)
    try:
        tracker.db.upsert_attempt(attempt())
        population.rows = [{**row, "player_name": "You"} for row in population.rows]
        rivals.choose_library(TARGET, [AttemptGhost(1, "star", "2-1", "Standard", 600)], True)
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        rows = rivals.payload()["rows"]
        own = [row for row in rows if row["own"]]
        assert len(own) == 1 and own[0]["pinned"] and own[0]["player_id"] == "local:self"
        assert [row["player_name"] for row in rows if not row["own"]] and all(
            row["player_name"] == "You" for row in rows if not row["own"])
    finally:
        tracker.db.close()


def test_a_run_with_no_strategy_never_joins_the_standard_lineup(tmp_path, monkeypatch):
    rivals, tracker, _, _, _ = controller(tmp_path, monkeypatch)
    try:
        run = attempt(strat_tag=None)
        tracker.db.upsert_attempt(run)
        before = json.dumps(rivals.pins, sort_keys=True)
        assert not rivals.remember_saved({**TARGET, "strat": None}, run)
        from sm64_events.ghost.pb import local_attempt_source
        identity = rivals._remember_local(local_attempt_source(run))
        assert rivals._private_record(identity, rivals.identity) is None  # not "Standard" by default
        assert json.dumps(rivals.pins, sort_keys=True) == before
    finally:
        tracker.db.close()

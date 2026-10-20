"""Library choices persist through the same owner without adopting its target."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sm64_events.ghost.rivals import Rivals, STATE_KEY
from tests.test_ghost_adaptive import SharedStandards, recording
from tests.test_ghost_rivals import CONTROLS, TARGET, controller

OTHER = {**TARGET,"entity_key":"2-2"}
UID = "11111111-1111-4111-8111-111111111111"


def live_state(rivals, native):
    return (rivals.target,rivals.identity,rivals.rows,rivals.pb,rivals.fingerprint,
            rivals.prepared_key,rivals.generation,rivals.manual,rivals.enabled,
            rivals.redeal,dict(rivals.options),len(native.preparations),native.canceled)


def other_record(population, number=90, **values):
    row = recording(f"{number:064x}",**({"entity_key":OTHER["entity_key"]}|values))
    population.rows.append(row)
    return row


def test_save_b_while_a_is_active_survives_settings_and_restart_without_touching_a(tmp_path, monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    row = other_record(population)
    before = live_state(rivals,native)
    try:
        result = rivals.change_saved(OTHER,{"action":"add","id":row["id"]})
        assert result["state"] == "added" and result["slot"] == 0
        assert result["target"] == OTHER and result["scope"] == "strategy"
        assert result["pins"] == {"0":row["id"]}
        assert result["seats"][0]["player_name"] == row["player_name"]
        assert result["seats"][0]["frames"] == row["frames"]
        assert live_state(rivals,native) == before
        # Even the next normal watch cycle must not re-read/re-deal target A.
        monkeypatch.setattr(rivals,"_desired",lambda *_:pytest.fail("Inactive save invalidated Practice"))
        rivals.refresh(TARGET,CONTROLS,enabled=True)
        assert live_state(rivals,native) == before
        monkeypatch.undo()
        rivals.apply({"options":{**rivals.options,"count":2}})
        assert rivals.pins[rivals._key(OTHER)] == {"0":row["id"]}
        restored = Rivals(tracker,population,native,lambda:"us")
        restored.refresh(OTHER,CONTROLS,enabled=True)
        assert restored.options["count"] == 2
        assert restored.rows[0]["id"] == row["id"] and restored.rows[0]["pinned"]
    finally:
        tracker.db.close()


@pytest.mark.parametrize("values",[
    {"entity_key":"2-3"},{"clock":"rta"},{"game_version":"jp"},
    {"strategy":"Other"},{"valid":False},{"full_start":False},
    {"player_id":""},{"player_name":""},{"frames":0},
])
def test_incompatible_record_cannot_mutate_any_saved_or_live_state(tmp_path, monkeypatch, values):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    row = other_record(population,**values)
    before = live_state(rivals,native),deepcopy(rivals.pins),tracker.db.get_state(STATE_KEY,None)
    try:
        with pytest.raises(ValueError,match="compatible"):
            rivals.change_saved(OTHER,{"action":"add","id":row["id"]})
        assert (live_state(rivals,native),rivals.pins,tracker.db.get_state(STATE_KEY,None)) == before
    finally:
        tracker.db.close()


def test_full_pins_request_explicit_slot_and_keep_hidden_and_other_namespace(tmp_path, monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    rows = [other_record(population,number) for number in range(90,95)]
    key = rivals._key(OTHER)
    rivals.options["count"] = 2
    rivals.pins[key] = {"0":rows[0]["id"],"1":rows[1]["id"],"3":rows[3]["id"]}
    rivals.pins[rivals._key(TARGET)] = {"2":population.rows[0]["id"]}
    rivals._store()
    before = tracker.db.get_state(STATE_KEY,None)
    try:
        result = rivals.change_saved(OTHER,{"action":"add","id":rows[4]["id"]})
        assert result["state"] == "needs_slot" and result["slot"] is None
        assert [seat["pinned"] for seat in result["seats"]] == [True,True,False,True]
        assert result["seats"][3]["visible"] is False
        assert tracker.db.get_state(STATE_KEY,None) == before
        result = rivals.change_saved(OTHER,{"action":"add","id":rows[4]["id"],"slot":1})
        assert result["state"] == "added" and result["slot"] == 1
        assert result["pins"] == {"0":rows[0]["id"],"1":rows[4]["id"],"3":rows[3]["id"]}
        assert rivals.pins[rivals._key(TARGET)] == {"2":population.rows[0]["id"]}
        with pytest.raises(ValueError,match="visible"):
            rivals.change_saved(OTHER,{"action":"add","id":rows[2]["id"],"slot":3})
    finally:
        tracker.db.close()


def test_own_recordings_cannot_be_added_or_replaced_but_equal_names_are_allowed(tmp_path, monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    rows = [other_record(population,number,player_name="Same name",player_id=player)
            for number,player in ((90,"local-owner"),(91,UID),(92,"stranger"),(93,UID))]
    tracker.db.set_state("ghost_player_id","local-owner")
    rivals.community.sync = SimpleNamespace(refused_assets=frozenset,
        account=SimpleNamespace(snapshot=lambda:{
            "authenticated":True,"status":"active","uid":UID,"name":"Same name"}))
    rivals.options["count"] = 1
    rivals._store()
    try:
        before = deepcopy(rivals.pins),tracker.db.get_state(STATE_KEY,None),live_state(rivals,native)
        for own in (rows[0],rows[1],rows[3]):
            for slot in (None,0):
                with pytest.raises(ValueError,match="PB ghost is included"):
                    rivals.change_saved(OTHER,{"action":"add","id":own["id"],"slot":slot})
                assert (rivals.pins,tracker.db.get_state(STATE_KEY,None),live_state(rivals,native)) == before
        result = rivals.change_saved(OTHER,{"action":"add","id":rows[2]["id"]})
        assert result["state"] == "added" and not result["seats"][0]["own"]
        # Old own pins remain removable even when hidden.
        rivals.pins[rivals._key(OTHER)]["3"] = rows[0]["id"]
        rivals._store()
        result = rivals.change_saved(OTHER,{"action":"remove","id":rows[0]["id"]})
        assert result["state"] == "removed" and result["slot"] == 3
        assert result["pins"] == {"0":rows[2]["id"]}
    finally:
        tracker.db.close()


def test_remove_unavailable_hidden_choice_is_safe_and_does_not_need_metadata(tmp_path, monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    row = other_record(population)
    missing = "f"*64
    rivals.pins[rivals._key(OTHER)] = {"0":missing,"3":row["id"]}
    before = live_state(rivals,native)
    try:
        response = rivals.change_saved(OTHER,{"action":"remove","id":missing})
        assert response["state"] == "removed" and response["pins"] == {"3":row["id"]}
        assert live_state(rivals,native) == before
        with pytest.raises(ValueError,match="changed"):
            rivals.change_saved(OTHER,{"action":"remove","id":row["id"],"slot":0})
        response = rivals.change_saved(OTHER,{"action":"remove","id":row["id"]})
        assert response["slot"] == 3 and response["pins"] == {}
        assert live_state(rivals,native) == before
    finally:
        tracker.db.close()


def test_missing_pin_is_protected_in_slot_picker_without_fabricated_player(tmp_path, monkeypatch):
    rivals,tracker,_,population,_ = controller(tmp_path,monkeypatch)
    row = other_record(population)
    rivals.options["count"] = 1
    rivals.pins[rivals._key(OTHER)] = {"0":"f"*64}
    try:
        result = rivals.change_saved(OTHER,{"action":"add","id":row["id"]})
        seat = result["seats"][0]
        assert result["state"] == "needs_slot" and seat["pinned"] and not seat["available"]
        assert "player_id" not in seat and "player_name" not in seat and seat["reason_text"]
    finally:
        tracker.db.close()


def test_community_choice_keeps_owner_provenance_and_removal_only_prunes_unused_choice(tmp_path, monkeypatch):
    rivals,tracker,_,population,_ = controller(tmp_path,monkeypatch)
    rows = [other_record(population,number,certified=False,structurally_valid=True,
                         provenance="community-unverified") for number in (90,91)]
    rivals.community.sync = SimpleNamespace(refused_assets=frozenset,
        account=SimpleNamespace(snapshot=lambda:{"authenticated":True,"status":"active","uid":UID}))
    try:
        for row in rows:
            rivals.change_saved(OTHER,{"action":"add","id":row["id"]})
        stored = tracker.db.get_state(STATE_KEY,{})
        assert stored["remote_choices"] == {row["id"]:UID for row in rows}
        rivals.change_saved(OTHER,{"action":"remove","id":rows[0]["id"]})
        assert tracker.db.get_state(STATE_KEY,{})["remote_choices"] == {rows[1]["id"]:UID}
        restored = Rivals(tracker,population,rivals.native,lambda:"us",community=rivals.community.sync)
        restored._load()
        assert restored.pins == rivals.pins and restored.remote_choices == {rows[1]["id"]:UID}
    finally:
        tracker.db.close()


def test_explicit_scope_and_rom_reuse_real_grading_without_changing_view(tmp_path, monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    row = other_record(population,game_version="jp",strategy="Other")
    rivals.scopes[rivals._entity_key(OTHER)] = "overall"
    before = live_state(rivals,native),dict(rivals.scopes)
    try:
        with pytest.raises(ValueError,match="compatible"):
            rivals.change_saved(OTHER,{"action":"add","id":row["id"],"scope":"strategy"},version="jp")
        result = rivals.change_saved(OTHER,{"action":"add","id":row["id"],"scope":"overall"},version="jp")
        assert result["scope"] == "overall" and rivals._scope_key(OTHER,"overall") in rivals.pins
        assert (live_state(rivals,native),rivals.scopes) == before
        assert result["seats"][0]["strategy"] == "Other"
        assert "ghost" not in json.dumps(result) and "_owned" not in json.dumps(result)
    finally:
        tracker.db.close()


def test_shared_region_accepts_other_rom_but_does_not_forge_it(tmp_path, monkeypatch):
    rivals,tracker,_,population,_ = controller(tmp_path,monkeypatch)
    tracker.ranks = SharedStandards()
    row = other_record(population,game_version="jp")
    try:
        assert rivals.change_saved(OTHER,{"action":"add","id":row["id"]},version="us")["state"] == "added"
        assert population.get(row["id"])["game_version"] == "jp"
    finally:
        tracker.db.close()


def test_segment_signature_comes_from_local_definition_and_rejects_foreign_id(tmp_path, monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    target = {"kind":"segment","entity_key":"7","strat":"Standard"}
    definitions = [{"id":7,"name":"Local segment"}]
    monkeypatch.setattr(tracker.db,"segment_defs",lambda:definitions)
    row = recording("e"*64,kind="segment",entity_key="7",signature="f"*64)
    population.rows.append(row)
    try:
        with pytest.raises(ValueError,match="compatible"):
            rivals.change_saved(target,{"action":"add","id":row["id"]})
        row["signature"] = rivals.identity_for(target).signature
        assert rivals.change_saved(target,{"action":"add","id":row["id"]})["state"] == "added"
        assert not rivals.saved_availability({**target,"entity_key":"8"})["available"]
        assert live_state(rivals,native)[0] == TARGET
    finally:
        tracker.db.close()


def test_ghost_only_missing_ladder_preflight_and_add_give_same_reason_without_changes(tmp_path, monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    target = {**OTHER,"strat":"Missing"}
    row = other_record(population,strategy="Missing")
    before = live_state(rivals,native),deepcopy(rivals.pins)
    try:
        availability = rivals.saved_availability(target,version="us",scope="strategy")
        assert not availability["available"] and "no local rank standards" in availability["reason"]
        with pytest.raises(ValueError,match="no local rank standards"):
            rivals.change_saved(target,{"action":"add","id":row["id"]},version="us")
        assert (live_state(rivals,native),rivals.pins) == before
        assert rivals.saved_availability(OTHER,version="us") == {"available":True,"reason":None,"pins":{}}
        assert not rivals.saved_availability(OTHER,version="unknown")["available"]
    finally:
        tracker.db.close()


def test_preflight_compares_recording_clock_with_canonical_target_clock(tmp_path, monkeypatch):
    rivals,tracker,native,_,_ = controller(tmp_path,monkeypatch)
    before = live_state(rivals,native),deepcopy(rivals.pins)
    try:
        assert rivals.saved_availability(OTHER,expected_clock="igt") == {"available":True,"reason":None,"pins":{}}
        refusal = rivals.saved_availability(OTHER,expected_clock="rta")
        assert refusal == {"available":False,"reason":"This ghost uses RTA; this target uses IGT.","pins":{}}
        assert not rivals.saved_availability(OTHER,expected_clock="unknown")["available"]
        assert (live_state(rivals,native),rivals.pins) == before
    finally:
        tracker.db.close()


def test_preflight_returns_detached_saved_pins_even_when_grading_or_clock_is_unavailable(tmp_path, monkeypatch):
    rivals,tracker,native,population,_ = controller(tmp_path,monkeypatch)
    row = other_record(population)
    target = {**OTHER,"strat":"Missing"}
    rivals.pins[rivals._scope_key(OTHER,"strategy")] = {"3":row["id"]}
    rivals.pins[rivals._scope_key(target,"strategy")] = {"1":row["id"]}
    before = live_state(rivals,native),deepcopy(rivals.pins)
    try:
        availability = rivals.saved_availability(OTHER,expected_clock="rta")
        assert not availability["available"] and availability["pins"] == {"3":row["id"]}
        availability["pins"].clear()
        missing_ladder = rivals.saved_availability(target)
        assert not missing_ladder["available"] and missing_ladder["pins"] == {"1":row["id"]}
        invalid = rivals.saved_availability(OTHER,version="unknown")
        assert not invalid["available"] and invalid["pins"] == {}
        assert (live_state(rivals,native),rivals.pins) == before
    finally:
        tracker.db.close()


@pytest.mark.parametrize("body",[
    None,{},[],{"action":"add","id":"not-a-hash"},
    {"action":"add","id":"f"*64,"slot":True},
    {"action":"add","id":"f"*64,"scope":"foreign"},
    {"action":"add","id":"f"*64,"scope":None},
    {"action":"replace","id":"f"*64},
    {"action":"add","id":"f"*64,"options":{"count":1}},
])
def test_saved_command_refuses_malformed_requests_without_partial_writes(tmp_path, monkeypatch, body):
    rivals,tracker,native,_,_ = controller(tmp_path,monkeypatch)
    before = live_state(rivals,native),tracker.db.get_state(STATE_KEY,None)
    try:
        with pytest.raises(ValueError):
            rivals.change_saved(OTHER,body)
        assert (live_state(rivals,native),tracker.db.get_state(STATE_KEY,None)) == before
    finally:
        tracker.db.close()

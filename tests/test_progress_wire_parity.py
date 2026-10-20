"""Actual cloud admission and Python reading consume one JSON content corpus.

The cloud may require canonical writer order while readers accept unique historic
orders. UTC is tested in the writer's canonical Z/+00:00 subset. No replica of
either validation algorithm decides acceptance here.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from sm64_events.community.config import CommunityError
from sm64_events.community.progress_snapshot import checked_snapshot, project, public_segments
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.sharing_targets import canonical
from test_progress_snapshot import ACHIEVED, SAVED, pb, service

ROOT = Path(__file__).resolve().parents[1]
UID = "10000000-0000-4000-8000-000000000001"
PUBLISHER = {"installation_id": "20000000-0000-4000-8000-000000000001",
             "profile_id": "30000000-0000-4000-8000-000000000001"}


def scalar_rejections(row):
    for name, updates in [
        ("frames-over-bound", {"frames": 2592001}),
        ("source-cs-over-bound", {"source_cs": 8640001}),
        ("frames-zero", {"frames": 0}),
        ("frames-boolean", {"frames": True}),
        ("source-cs-boolean", {"source_cs": True}),
        ("save-order-boolean", {"save_order": True}),
        ("unknown-clock", {"clock": "wall"}),
        ("invalid-raw-rom", {"game_version": "auto"}),
        ("conflicting-raw-and-effective-rom", {"game_version": "us", "pb_version": "jp"}),
        ("missing-effective-stamped-rom", {"game_version": "us", "pb_version": None}),
        ("empty-strategy", {"strategy": ""}),
        ("strategy-ascii-control", {"strategy": "Route\nA"}),
        ("unpaired-surrogate-strategy", {"strategy": "Route\ud800"}),
        ("nonplayed-achievement-date", {"source_kind": "manual", "achieved_utc": ACHIEVED}),
        ("naive-save-date", {"saved_utc": "2026-10-19T12:34:56"}),
        ("impossible-calendar-date", {"saved_utc": "2026-02-30T12:34:56Z"}),
        ("year-zero-date", {"saved_utc": "0000-01-01T00:00:00Z"}),
        ("invalid-source-kind", {"source_kind": "verified"}),
    ]:
        row(name, **updates)


def shape_rejections(add, coverage):
    coverage("coverage-over-bound", total_rows=2147483648, withheld_rows=2147483647, complete=False)
    coverage("coverage-boolean", shared_rows=True)
    coverage("coverage-incoherent-complete", complete=False)
    add("extra-content-private-field", lambda value: value.update(attempt_id=500))
    add("extra-target-strat-key", lambda value: value["rows"][0]["target"].update(strat=None))
    add("extra-star-signature-key", lambda value: value["rows"][0]["target"].update(signature=None))
    add("missing-public-name-key", lambda value: value["rows"][0]["target"].pop("name"))
    add("unknown-star-slot", lambda value: value["rows"][0]["target"].update(entity_key="1-7"))
    add("noncanonical-star-key", lambda value: value["rows"][0]["target"].update(entity_key="01-0"))
    add("segment-private-local-id", lambda value: value["rows"][0].update(target={**next(iter(public_segments().values())), "entity_key": "42"}))
    add("segment-invalid-signature", lambda value: value["rows"][0].update(target={**next(iter(public_segments().values())), "signature": "bad"}))


def corpus():
    base = project(service([pb()]))
    cases = []
    def add(name, edit=None, *, accepted=False, cloud=None, value=None, writer=False):
        content = deepcopy(base if value is None else value)
        if edit:
            edit(content)
        cases.append({"name": name, "content": content, "python": accepted,
                      "cloud": accepted if cloud is None else cloud, "writer": writer})
    def row(name, **updates):
        add(name, lambda value: value["rows"][0].update(updates))
    def coverage(name, **updates):
        add(name, lambda value: value["coverage"].update(updates))

    add("canonical-current-writer", accepted=True, writer=True)
    add("writer-raw-null-effective-jp", accepted=True, writer=True, value=project(service([pb(pb_version="jp")], version="jp")))
    add("writer-null-strategy", accepted=True, writer=True, value=project(service([pb(strat_tag=None)])))
    add("writer-unicode-strategy", accepted=True, writer=True, value=project(service([pb(strat_tag="壁キック ⚡")])) )
    add("writer-format-character-strategy", accepted=True, writer=True, value=project(service([pb(strat_tag="Route\u200bA")])) )
    add("writer-empty-profile", accepted=True, writer=True, value=project(service()))
    add("public-seed-segment", lambda value: value["rows"][0].update(target=next(iter(public_segments().values())), clock="rta"), accepted=True)
    add("reader-null-public-name", lambda value: value["rows"][0]["target"].update(name=None), accepted=True)
    add("played-utc-dates", lambda value: value["rows"][0].update(source_kind="played", achieved_utc=ACHIEVED, saved_utc=SAVED), accepted=True)
    add("utc-explicit-zero-offset", lambda value: value["rows"][0].update(saved_utc="2026-10-19T12:34:56+00:00"), accepted=True)
    add("frame-source-upper-bounds", lambda value: value["rows"][0].update(frames=2592000, source_cs=8640000, source_kind="manual"), accepted=True)
    add("coverage-upper-bound", lambda value: value["coverage"].update(total_rows=2147483647, withheld_rows=2147483646, complete=False), accepted=True)
    scalar_rejections(row)
    shape_rejections(add, coverage)
    pair = deepcopy(base)
    pair["rows"].append({**deepcopy(pair["rows"][0]), "strategy": "Other", "save_order": 2})
    pair["coverage"].update(total_rows=2, shared_rows=2)
    add("canonical-two-saved-slots", value=pair, accepted=True)
    duplicate = deepcopy(pair)
    duplicate["rows"][1]["strategy"] = duplicate["rows"][0]["strategy"]
    add("duplicate-saved-slot", value=duplicate)
    duplicate_order = deepcopy(pair)
    duplicate_order["rows"][1]["save_order"] = 1
    add("duplicate-save-order", value=duplicate_order)
    reader_order = deepcopy(pair)
    reader_order["rows"].reverse()
    add("reader-unsorted-unique-order", value=reader_order, accepted=True, cloud=False)
    add("reader-unique-order-gap", lambda value: value["rows"][0].update(save_order=3), accepted=True, cloud=False)
    too_many = deepcopy(base)
    too_many["rows"] = [{**deepcopy(base["rows"][0]), "strategy": f"Method {index}", "save_order": index + 1} for index in range(4097)]
    too_many["coverage"].update(total_rows=4097, shared_rows=4097)
    add("row-count-over-bound", value=too_many)
    too_wide = deepcopy(base)
    too_wide["rows"] = [{**deepcopy(base["rows"][0]), "strategy": "星" * 82 + f"{index:04d}", "save_order": index + 1} for index in range(2300)]
    too_wide["coverage"].update(total_rows=2300, shared_rows=2300)
    assert len(canonical(too_wide)) > 1048576
    add("utf8-byte-count-over-bound", value=too_wide)
    return cases


def cloud_batch(cases):
    node = shutil.which("node")
    assert node is not None, "Node is required to exercise the actual cloud validator"
    requests = []
    for case in cases:
        try:
            digest = hashlib.sha256(canonical(case["content"])).hexdigest()
        except UnicodeEncodeError:
            digest = "0" * 64  # Illegal UTF-8 has no canonical content hash.
        requests.append({"name": case["name"], "uid": UID, "input": {
            "uid": UID, "expected_revision": 1, "consent_revision": 1, "publisher": PUBLISHER,
            "captured_utc": SAVED, "content_hash": digest, "content": case["content"]}})
    script = """
import {snapshot} from './cloud/community/src/player-validation.js';
let input='';for await(const chunk of process.stdin)input+=chunk;
const results=[];for(const item of JSON.parse(input)){
  try{const value=await snapshot(item.input,item.uid);results.push({name:item.name,accepted:true,body:value.body});}
  catch(error){results.push({name:item.name,accepted:false,code:error.code||'unexpected',status:error.status||0});}
}process.stdout.write(JSON.stringify(results));
"""
    result = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT, input=json.dumps(requests, ensure_ascii=True),
                            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True, **quiet_spawn_kwargs())
    return json.loads(result.stdout)


def test_actual_cloud_admission_and_python_reader_share_the_bounded_snapshot_contract():
    cases = corpus()
    cloud = cloud_batch(cases)
    assert len(cloud) == len(cases)
    mismatches = []
    for case, admitted in zip(cases, cloud, strict=True):
        try:
            checked_snapshot(case["content"])
            python = True
        except (CommunityError, ValueError, TypeError):
            python = False
        if python != case["python"] or admitted["accepted"] != case["cloud"]:
            mismatches.append((case["name"], {"python": python, "cloud": admitted, "expected_python": case["python"], "expected_cloud": case["cloud"]}))
        if admitted["accepted"]:
            checked_snapshot(json.loads(admitted["body"]))  # Read the exact canonical body the service will retain.
        if case["writer"]:
            assert python and admitted["accepted"], case["name"]
    assert not mismatches, [(name, {**details, "cloud": {key: value for key, value in details["cloud"].items() if key != "body"}})
                            for name, details in mismatches]

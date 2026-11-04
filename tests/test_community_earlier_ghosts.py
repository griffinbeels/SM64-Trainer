"""Earlier ghosts upload with no question, under the first account that shares.

His decision, 2026-11-03: "No question. Just upload automatically", and "those
should get associated with his profile." Only terms that disclose it allow it.
"""
import asyncio
import hashlib
from pathlib import Path
import re
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.community.earlier_ghosts import OLDER_TERMS, REMOVED, EarlierClaim, own_sources as _own_sources
from sm64_events.ghost.rival_recording import PopulationRecorder
from sm64_events.server.community_api import create_community_router
from test_community_outbox import Account, UID, native, settled, source, sync_at
from test_community_sync import catalog

OTHER = "22222222-2222-4222-8222-222222222222"
ME = "me-on-this-install"
CLOUD = Path(__file__).resolve().parents[1] / "cloud" / "community"


def own_sources(rows, player_id, root, still_good=lambda _row: True):
    return _own_sources(rows, player_id, root, still_good=still_good)


def row(tmp_path, **changes):
    raw = native(3)
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / "clips").mkdir(exist_ok=True)
    (tmp_path / "clips" / f"{digest}.ghost").write_bytes(raw)
    made = source(tmp_path / "unused.ghost")
    return {"player_id": ME, "target": made["target"], "clock": "igt", "game_version": "us", "strategy": "Standard",
            "frames": 3, "sha256": digest, "certified": True, "valid": True, "full_start": True,
            "source": {"session_id": 1, "attempt_id": 1, "started_utc": "2026-10-28T21:00:00Z"}, **changes}


def account_on(terms="beta-2", uid=None):
    account = Account()
    account.state["terms_version"] = terms
    if uid:
        account.state["uid"] = uid
    return account


def sync_with(tmp_path, account=None):
    sync = sync_at(tmp_path, account or account_on())
    sync.earlier_sources = lambda: own_sources([row(tmp_path)], ME, tmp_path)
    return sync


def test_only_this_installs_own_recorded_runs_qualify(tmp_path):
    good = row(tmp_path)
    assert [item["raw_sha256"] for item in own_sources([good], ME, tmp_path)] == [good["sha256"]]
    for bad in ({"player_id": "someone-imported"}, {"source": None}, {"certified": False}, {"simulated": True},
                {"valid": False}, {"full_start": False}, {"strategy": None}, {"sha256": "0" * 64}):
        assert not own_sources([row(tmp_path, **bad)], ME, tmp_path), bad


def test_the_recorder_offers_rows_from_its_local_store(tmp_path):
    recorder, community = recorder_over(tmp_path, attempt())
    assert community.earlier_sources == recorder.earlier_sources
    assert len(recorder.earlier_sources()) == 1


def attempt(**changes):
    """The run behind `row`, as today's rules read it: a clean star run."""
    return SimpleNamespace(**{"id": 1, "session_id": 1, "started_utc": "2026-10-28T21:00:00Z", "strat_tag": "Standard",
        "valid": True, "outcome": "success", "cleared": False, "timed_by": "igt", "timed_at": "xcam",
        "game_version": "us", "native_clock_kind": "star", "segment_id": None, "course_id": 2, "star_id": 1,
        "igt_frames": 3, "rta_frames": 3, "anchor_type": "reset", "state_loads": 0, "invalid_reason": None,
        "ended_utc": "2026-10-28T21:01:00Z", "native_clock_frame": 10, "closed_frame": 20,
        "native_clock_utc": "2026-10-28T21:00:00+00:00", **changes})


def recorder_over(tmp_path, run, identity="identity", ranks=None):
    store = SimpleNamespace(root=tmp_path, snapshot_all=lambda: (row(tmp_path), row(tmp_path, player_id="other")),
                            rows_for_source=lambda _source: ())
    tracker = SimpleNamespace(db=SimpleNamespace(get_state=lambda key, default=None: ME), ranks=ranks)

    def lookup(_attempt_id):
        if run is None:
            raise LookupError("no attempt")
        return run
    community = SimpleNamespace()
    recorder = PopulationRecorder(store, SimpleNamespace(attempt=lookup), tracker, lambda _attempt: identity,
                                  community=community)
    return recorder, community


def test_a_run_that_stopped_passing_todays_rules_stays_home(tmp_path):
    """Griffin, 2026-11-04: "Just making sure we're not pushing any *broken* old ghosts."
    The row says valid; only the run's current state can say it still is."""
    for broken in ({"valid": False}, {"outcome": "reset"}, {"cleared": True}, {"state_loads": 1},
                   {"anchor_type": "state_loaded"}, {"timed_by": "imported"}, {"timed_at": "grab"},
                   {"igt_frames": 4}, {"strat_tag": "Other"}, {"session_id": 2}, {"game_version": "jp"}):
        recorder, _ = recorder_over(tmp_path, attempt(**broken))
        assert recorder.earlier_sources() == (), broken
    for gone in (recorder_over(tmp_path, None), recorder_over(tmp_path, attempt(), identity=None)):
        assert gone[0].earlier_sources() == ()


def test_a_store_that_is_not_ready_claims_nothing(tmp_path):
    sync = sync_with(tmp_path)
    sync.earlier_sources = lambda: None
    sync._share_earlier_ghosts()
    assert EarlierClaim(sync.population.root).read() is None


def test_they_upload_with_no_question_under_this_account(tmp_path):
    async def scenario():
        account = account_on()
        sent = []

        async def answer(method, path, **kwargs):
            sent.append((method, path, kwargs.get("json")))
            if path == "/v1/catalog":
                return catalog()
            if method == "POST":
                return {"disposition": "already_present", "recording_id": "x"}
            raise AssertionError(path)
        account.request_json = answer
        sync = sync_with(tmp_path, account)
        await sync.start()
        sync._practice_started.set()
        try:
            await settled(lambda: any(call[1] == "/v1/uploads" for call in sent))
            await settled(lambda: not sync._outbox.pending(UID))
        finally:
            await sync.stop()
        upload = next(call[2] for call in sent if call[1] == "/v1/uploads")
        assert upload["frames"] == 3 and upload["strategy"] == "Standard"
        assert "path" not in upload  # No local file path leaves this computer.
    asyncio.run(scenario())


def test_older_or_unknown_terms_never_adopt_them(tmp_path):
    for terms in ("beta-1", None):
        sync = sync_with(tmp_path, account_on(terms))
        sync._share_earlier_ghosts()
        assert not sync._outbox.pending(UID) and EarlierClaim(sync.population.root).read() is None


def test_the_published_terms_disclose_it_and_pass_the_gate():
    settings = (CLOUD / "wrangler.jsonc").read_text(encoding="utf-8")
    versions = re.findall(r'"(?:TERMS|PRIVACY)_VERSION": "([^"]+)"', settings)
    assert len(versions) == 2 and not OLDER_TERMS.intersection(versions)
    policy = (CLOUD / "src" / "policies.js").read_text(encoding="utf-8")
    assert "before you signed up" in policy and "future eligible" not in policy


def test_only_the_first_account_gets_them(tmp_path):
    sync_with(tmp_path)._share_earlier_ghosts()
    second = sync_with(tmp_path, account_on(uid=OTHER))
    second._share_earlier_ghosts()
    assert not second._outbox.pending(OTHER)


def test_sharing_off_adopts_nothing(tmp_path):
    account = account_on()
    account.state["sharing"] = False
    sync = sync_with(tmp_path, account)
    sync._share_earlier_ghosts()
    assert not sync._outbox.pending(UID) and EarlierClaim(sync.population.root).read() is None


def test_cancelling_stops_them_until_the_same_account_shares_again(tmp_path):
    async def scenario():
        sync = sync_with(tmp_path)
        sync._share_earlier_ghosts()
        assert sync._outbox.pending(UID)
        await sync.cancel_contributions(UID)
        assert not sync._outbox.pending(UID)
        other = sync_with(tmp_path, account_on(uid=OTHER))
        other._share_earlier_ghosts()
        assert not other._outbox.pending(OTHER)
        back = sync_with(tmp_path)
        back._share_earlier_ghosts()
        assert back._outbox.pending(UID)
    asyncio.run(scenario())


def test_once_queued_they_are_not_queued_again(tmp_path):
    sync = sync_with(tmp_path)
    sync._share_earlier_ghosts()
    sync._outbox.discard(owner=UID)
    sync._share_earlier_ghosts()
    assert not sync._outbox.pending(UID)


def test_removing_shared_ghosts_closes_the_claim_for_good(tmp_path):
    async def scenario():
        sync = sync_with(tmp_path)
        sync._share_earlier_ghosts()
        sync.close_earlier_ghosts()
        await sync.cancel_contributions(UID)
        assert EarlierClaim(sync.population.root).read()["state"] == REMOVED
        back = sync_with(tmp_path)
        back._share_earlier_ghosts()
        assert not back._outbox.pending(UID)
    asyncio.run(scenario())


def test_the_remove_button_closes_the_claim_before_the_cloud_is_asked(tmp_path):
    order = []

    class Remote:
        def snapshot(self):
            return {"enabled": True}

        async def request_json(self, method, path, **_kwargs):
            order.append(path)
            return {"withdrawn": True}
    sync = SimpleNamespace(close_earlier_ghosts=lambda: order.append("closed"))
    app = FastAPI()
    app.include_router(create_community_router(Remote(), sync=sync))
    assert TestClient(app).post("/api/community/account/withdraw", json={"confirmation": "WITHDRAW"}).status_code == 200
    assert order == ["closed", "/v1/account/withdraw"]


def test_a_damaged_claim_file_reads_as_no_claim(tmp_path):
    claim = EarlierClaim(tmp_path)
    claim.path.write_text("not json")
    assert claim.read() is None and claim.open_to(UID)

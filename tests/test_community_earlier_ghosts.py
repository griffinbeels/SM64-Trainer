"""Earlier ghosts go up only on the player's yes, and only under one account.

His decision, 2026-11-03: ghosts earned before sign-in upload, "and then those
should get associated with his profile."
"""
import asyncio
import hashlib
from types import SimpleNamespace

from sm64_events.community.earlier_ghosts import EarlierAnswer, own_sources
from sm64_events.ghost.rival_recording import PopulationRecorder
from test_community_outbox import Account, UID, native, settled, source, sync_at
from test_community_sync import catalog

OTHER = "22222222-2222-4222-8222-222222222222"
ME = "me-on-this-install"


def row(tmp_path, **changes):
    raw = native(3)
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / "clips").mkdir(exist_ok=True)
    (tmp_path / "clips" / f"{digest}.ghost").write_bytes(raw)
    made = source(tmp_path / "unused.ghost")
    return {"player_id": ME, "target": made["target"], "clock": "igt", "game_version": "us", "strategy": "Standard",
            "frames": 3, "sha256": digest, "certified": True, "valid": True, "full_start": True,
            "source": {"session_id": 1, "attempt_id": 1, "started_utc": "2026-10-28T21:00:00Z"}, **changes}


def sync_with(tmp_path, account=None):
    sync = sync_at(tmp_path, account or Account())
    sync.earlier_sources = lambda: own_sources([row(tmp_path)], ME, tmp_path)
    return sync


def other_account():
    account = Account()
    account.state["uid"] = OTHER
    return account


def test_only_this_installs_own_recorded_runs_qualify(tmp_path):
    good = row(tmp_path)
    assert [item["raw_sha256"] for item in own_sources([good], ME, tmp_path)] == [good["sha256"]]
    for bad in ({"player_id": "someone-imported"}, {"source": None}, {"certified": False}, {"simulated": True},
                {"valid": False}, {"full_start": False}, {"strategy": None}, {"sha256": "0" * 64}):
        assert not own_sources([row(tmp_path, **bad)], ME, tmp_path), bad


def test_the_recorder_offers_rows_from_its_local_store(tmp_path):
    store = SimpleNamespace(root=tmp_path, snapshot_all=lambda: (row(tmp_path), row(tmp_path, player_id="other")),
                            rows_for_source=lambda _source: ())
    tracker = SimpleNamespace(db=SimpleNamespace(get_state=lambda key, default=None: ME))
    community = SimpleNamespace()
    recorder = PopulationRecorder(store, None, tracker, None, community=community)
    assert community.earlier_sources == recorder.earlier_sources
    assert len(recorder.earlier_sources()) == 1


def test_a_login_alone_uploads_nothing_and_asks_once(tmp_path):
    sync = sync_with(tmp_path)
    assert sync.earlier_ghosts() == {"ask": True, "offer": True}
    assert not sync._outbox.pending(UID)
    assert sync.answer_earlier_ghosts(False) == {"ask": False, "offer": True}
    assert not sync._outbox.pending(UID)
    # A later start does not ask again, and Settings can still share.
    assert sync_with(tmp_path).earlier_ghosts() == {"ask": False, "offer": True}


def test_yes_uploads_each_ghost_under_this_account(tmp_path):
    async def scenario():
        account = Account()
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
        try:
            assert await asyncio.to_thread(sync.answer_earlier_ghosts, True) == {"ask": False, "offer": False}
            await settled(lambda: any(call[1] == "/v1/uploads" for call in sent))
            await settled(lambda: not sync._outbox.pending(UID))
        finally:
            await sync.stop()
        upload = next(call[2] for call in sent if call[1] == "/v1/uploads")
        assert upload["frames"] == 3 and upload["strategy"] == "Standard"
        assert "path" not in upload  # No local file path leaves this computer.
    asyncio.run(scenario())


def test_after_one_accounts_yes_no_other_account_can_share_them(tmp_path):
    sync_with(tmp_path).answer_earlier_ghosts(True)
    second = sync_with(tmp_path, other_account())
    assert second.earlier_ghosts() == {"ask": False, "offer": False}
    second.answer_earlier_ghosts(True)
    assert not second._outbox.pending(OTHER)


def test_not_now_from_one_account_still_lets_another_answer_for_itself(tmp_path):
    sync_with(tmp_path).answer_earlier_ghosts(False)
    assert sync_with(tmp_path, other_account()).earlier_ghosts() == {"ask": True, "offer": True}


def test_sharing_off_is_never_asked_and_uploads_nothing(tmp_path):
    account = Account()
    account.state["sharing"] = False
    sync = sync_with(tmp_path, account)
    assert sync.earlier_ghosts() == {"ask": False, "offer": False}
    sync.answer_earlier_ghosts(True)
    assert not sync._outbox.pending(UID) and EarlierAnswer(sync.population.root).read() is None


def test_cancelling_stops_them_and_only_the_button_restarts(tmp_path):
    async def scenario():
        sync = sync_with(tmp_path)
        sync.answer_earlier_ghosts(True)
        assert sync._outbox.pending(UID)
        await sync.cancel_contributions(UID)
        assert not sync._outbox.pending(UID)
        back = sync_with(tmp_path)
        back._outbox.load()
        assert not back._outbox.pending(UID)
        assert back.earlier_ghosts() == {"ask": False, "offer": True}
        assert sync_with(tmp_path, other_account()).earlier_ghosts() == {"ask": False, "offer": False}
        assert back.answer_earlier_ghosts(True) == {"ask": False, "offer": False}
        assert back._outbox.pending(UID)
    asyncio.run(scenario())


def test_a_damaged_answer_file_reads_as_no_answer(tmp_path):
    answer = EarlierAnswer(tmp_path)
    answer.path.write_text("not json")
    assert answer.read() is None and answer.asks(UID)

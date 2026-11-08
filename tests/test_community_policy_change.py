"""A new privacy notice on the live Worker, as this trainer meets it.

Griffin, 2026-11-08: "Sure, update privacy policy". The Worker that takes a
ghost's start serves privacy notice beta-3, which describes it; terms stay
beta-2. The fake Worker below answers the way cloud/community/src/service.js
does: GET/PUT /v1/account always answer, every other admitted route answers
403 consent_required until the account holds the current versions, and PUT
/v1/account stores only the current versions.

What must hold: the consent step comes back; uploads wait instead of failing
or dropping, including a run finished while the step is open; accepting once
sends them; and a start goes only from an account on the notice that
describes it.
"""
import asyncio
import json
import time as real_time

import httpx
import pytest

from sm64_events.community import CommunityAccount, ghost_start
from sm64_events.community import sync as sync_module
from sm64_events.community.outbox import SourceOutbox
from test_community_account import UID, Store, config, profile
from test_community_ghost_start import START, START_SHA, Service, _offer, _sent, _source
from test_community_outbox import settled, source, sync_at
from test_community_sync import catalog

CURRENT = {"terms_version": "beta-2", "privacy_version": "beta-3"}


class Clock:
    """The sync module's clock, movable past its 60 s recovery lease."""
    def __init__(self):
        self.offset = 0
    def monotonic(self):
        return real_time.monotonic() + self.offset
    def __getattr__(self, name):
        return getattr(real_time, name)


class Worker:
    def __init__(self):
        self.stored = {"terms_version": "beta-2", "privacy_version": "beta-2"}
        self.calls, self.uploads, self.grants = [], [], 0

    def account(self):
        return profile(sharing=True, **self.stored)

    async def __call__(self, request):
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        if path == "/v1/config":
            return httpx.Response(200, json={"v": 1, "protocol": 1, **CURRENT, "limits": {"start_bytes": 65536},
                "terms_url": "https://ghosts.example/policies#terms", "privacy_url": "https://ghosts.example/policies#privacy"})
        if path == "/v1/account" and method == "GET":
            return httpx.Response(200, json=self.account())
        if path == "/v1/account" and method == "PUT":
            body = json.loads(request.content)
            if {key: body[key] for key in CURRENT} != CURRENT:
                return httpx.Response(400, json={"error": {"code": "invalid_profile", "message": "x"}})
            self.stored = {key: body[key] for key in CURRENT}
            return httpx.Response(200, json=self.account())
        if self.stored != CURRENT:
            return httpx.Response(403, json={"error": {"code": "consent_required", "message": "Accept the current policies."}})
        if path == "/v1/catalog":
            return httpx.Response(200, json=catalog())
        if path == "/v1/uploads":
            self.grants += 1
            self.uploads.append({"admission": json.loads(request.content)})
            return httpx.Response(200, json={"v": 1, "disposition": "upload", "upload_id": f"grant-{self.grants}",
                                             "expires_at": int(real_time.time()) + 60})
        if path.startswith("/v1/uploads/grant-"):
            self.uploads[int(path.rsplit("-", 1)[1]) - 1]["body"] = request.content
            return httpx.Response(200, json={"v": 1, "disposition": "published", "recording_id": "1" * 64})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "x"}})


def on_disk(path):
    saved = SourceOutbox(path)
    saved.load()
    return {row["raw_sha256"] for row in saved.pending(UID)}


def test_a_new_notice_holds_uploads_until_accepted_once_then_sends_them_with_their_starts(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sync_module, "time", clock)
    monkeypatch.setattr(ghost_start, "start_for_upload", lambda path, digest: START)
    worker = Worker()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(worker)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            account._profile = {key: value for key, value in worker.account().items() if key != "v"}
            # A trainer that read beta-2 just before the Worker changed.
            account._policies = {"terms_version_current": "beta-2", "privacy_version_current": "beta-2"}
            account._policies_until = real_time.monotonic() + 3600
            sync = sync_at(tmp_path, account)
            before = source(tmp_path / "before.ghost")
            assert await asyncio.to_thread(sync.remember_source, before, **sync.contribution_context())
            await sync.start()
            try:
                # The first admitted call meets the 403; the consent step comes back.
                await settled(lambda: account.snapshot()["status"] == "consent_required")
                # A run finished now is saved for later, not sent and not lost. It
                # sits in another subdivision, so recovery keeps both (step 1, not 2).
                during = source(tmp_path / "during.ghost", frames=300)
                context = sync.contribution_context()
                assert context == {"owner": UID, "consent_epoch": sync._consent_epoch}
                assert await asyncio.to_thread(sync.remember_source, during, **context)
                assert not any(item[0] == "offer" for item in sync._queue.values())
                calls = len(worker.calls)
                clock.offset += 120
                for _ in range(3):
                    sync._contribution_wake.set()
                    await asyncio.sleep(0.05)
                assert len(worker.calls) == calls and not worker.uploads  # waits, no retry storm
                outbox = sync._outbox.path
                assert on_disk(outbox) == {before["raw_sha256"], during["raw_sha256"]}
                # The consent step: a refresh shows both versions; Continue sends the current ones.
                snapshot = await account.refresh()
                assert snapshot["status"] == "consent_required" and snapshot["privacy_version_current"] == "beta-3"
                accepted = await account.complete_profile(snapshot["name"], sharing=snapshot["sharing"],
                    terms_version=snapshot["terms_version_current"], privacy_version=snapshot["privacy_version_current"])
                assert accepted["status"] == "active" and worker.stored == CURRENT
                # Accepting once sends both, each with its start.
                clock.offset += 61
                sync._contribution_wake.set()
                await settled(lambda: len(worker.uploads) == 2 and all("body" in item for item in worker.uploads))
                await settled(lambda: not sync._outbox.pending(UID))
                assert {item["admission"]["raw_sha256"] for item in worker.uploads} == {before["raw_sha256"], during["raw_sha256"]}
                for item in worker.uploads:
                    assert (item["admission"]["start_sha256"], item["admission"]["start_bytes"]) == (START_SHA, len(START))
                    assert item["body"].endswith(START) and len(item["body"]) == item["admission"]["encoded_bytes"] + len(START)
                assert not on_disk(outbox)
            finally:
                await sync.stop()
    asyncio.run(scenario())


def test_a_run_finished_while_consent_is_open_is_not_kept_once_sharing_stops(tmp_path):
    account = Service()
    account.state["status"] = "consent_required"
    sync = sync_at(tmp_path, account)
    try:
        context = sync.contribution_context()
        assert context is not None
        account.state["sharing"] = False
        assert sync.contribution_context() is None
        assert not sync.remember_source(source(tmp_path / "run.ghost"), **context)
        assert not sync._outbox.pending(UID)
    finally:
        sync.population.close()


@pytest.mark.parametrize("accepted, sent", [("beta-2", False), ("beta-3", True), (None, False)])
def test_a_start_goes_only_from_an_account_on_the_notice_that_describes_it(tmp_path, monkeypatch, accepted, sent):
    monkeypatch.setattr(ghost_start, "start_for_upload", lambda path, digest: START)
    _, offer = _source(tmp_path)
    account = Service()
    account.state["privacy_version"] = accepted
    _offer(tmp_path, account, [offer])
    admissions, _ = _sent(account)
    assert ("start_sha256" in admissions[0]["json"]) is sent


def test_accepting_the_new_notice_is_seen_at_the_next_upload(tmp_path, monkeypatch):
    """A config read before the Worker changed (no cap, notice beta-2) does not
    hold starts back for its hour once the account has accepted beta-3."""
    clock = Clock()
    monkeypatch.setattr(sync_module, "time", clock)
    monkeypatch.setattr(ghost_start, "start_for_upload", lambda path, digest: START)
    _, offer = _source(tmp_path)
    account = Service(start_cap=None, notice="beta-2")
    account.state["privacy_version"] = "beta-2"
    population_root = tmp_path
    sync = sync_at(population_root, account)
    async def scenario():
        await sync._offer(dict(offer))
        account.start_cap, account.notice = 65536, "beta-3"   # the deploy
        account.state["privacy_version"] = "beta-3"           # he accepts
        await sync._offer(dict(offer))                        # within a minute: the held config stands
        clock.offset += 61
        await sync._offer(dict(offer))
    try:
        asyncio.run(scenario())
    finally:
        sync.population.close()
    admissions, _ = _sent(account)
    assert [("start_sha256" in item["json"]) for item in admissions] == [False, False, True]
    assert [call[1] for call in account.calls].count("/v1/config") == 2

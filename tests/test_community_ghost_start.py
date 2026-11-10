"""A ghost's start on the community wire: send it, keep it, pass it on.

The client carries the start without reading it. It sends one only when the
service advertises a cap (an older Worker refuses unknown upload fields), keeps
a downloaded one beside the cached ghost, and hands it to its owner. A bad or
missing start never costs the ghost itself.
"""
import asyncio
import hashlib

import httpx
import pytest

from sm64_events.community import CachedPopulation, CommunityAccount, CommunityError, CommunitySync
from sm64_events.community import cache, codec, ghost_start
from test_community_account import UID, Store, config
from test_community_codec import native
from test_community_sync import TARGET, Account, Local, recording

START = bytes(range(256)) * 102  # 26,112 bytes, the measured size of a start
START_SHA = hashlib.sha256(START).hexdigest()
UPLOAD_FIELDS = {"target", "clock", "rom", "strategy", "frames", "raw_sha256", "raw_bytes", "encoded_sha256",
                 "encoded_bytes", "codec", "generation"}


NOTICE = "beta-3"  # The first privacy notice that describes a ghost's start.


class Service(Account):
    """The fake Worker: a public config with or without a start cap, and ghost
    downloads that may name a start. The account has accepted its notice."""
    def __init__(self, *, start_cap=64 * 1024, served_start=None, notice=NOTICE):
        super().__init__()
        self.start_cap, self.served_start, self.notice = start_cap, served_start, notice
        self.state["privacy_version"] = NOTICE
        self.start_failure = None

    async def request_json(self, method, path, **kwargs):
        if path == "/v1/config":
            self.calls.append((method, path, kwargs))
            limits = {"encoded_bytes": 2 << 20} | ({} if self.start_cap is None else {"start_bytes": self.start_cap})
            return {"v": 1, "protocol": 1, "limits": limits, "terms_version": "beta-2", "privacy_version": self.notice}
        return await super().request_json(method, path, **kwargs)

    async def request_bytes(self, method, path, **kwargs):
        if path.endswith("/start"):
            self.calls.append((method, path, kwargs))
            if self.start_failure:
                raise self.start_failure
            return self.served_start
        encoded = await super().request_bytes(method, path, **kwargs)
        if self.served_start is not None and kwargs.get("response_headers") is not None:
            kwargs["response_headers"].update({"ghost-start-sha256": hashlib.sha256(self.served_start).hexdigest(),
                                               "ghost-start-bytes": str(len(self.served_start))})
        return encoded


def _source(tmp_path):
    raw = native()
    path = tmp_path / "native.ghost"
    path.write_bytes(raw)
    return raw, {"target": TARGET, "clock": "igt", "game_version": "us", "strategy": "Standard", "frames": 3,
                 "path": str(path), "raw_sha256": hashlib.sha256(raw).hexdigest(), "full_start": True,
                 "valid": True, "owner": UID}


def _offer(tmp_path, account, sources):
    population = CachedPopulation(Local(), root=tmp_path / "cache", account=account)
    sync = CommunitySync(account, population)
    async def scenario():
        for source in sources:
            await sync._offer(source)
    try:
        asyncio.run(scenario())
    finally:
        population.close()
    return sync


def _sent(account):
    admissions = [call[2] for call in account.calls if call[1] == "/v1/uploads"]
    puts = [call[2] for call in account.calls if call[1].startswith("/v1/uploads/")]
    return admissions, puts


def test_a_start_rides_after_the_ghost_when_the_service_accepts_one(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(ghost_start, "start_for_upload", lambda path, digest: asked.append((path, digest)) or START)
    raw, source = _source(tmp_path)
    account = Service()
    _offer(tmp_path, account, [source, dict(source)])
    admissions, puts = _sent(account)
    assert asked == [(source["path"], source["raw_sha256"])] * 2
    body = admissions[0]["json"]
    assert set(body) == UPLOAD_FIELDS | {"start_sha256", "start_bytes"}
    assert (body["start_sha256"], body["start_bytes"]) == (START_SHA, len(START))
    encoded = codec.encode(raw)
    assert puts[0]["content"] == encoded + START and body["encoded_bytes"] == len(encoded)
    # The public config is read once and held, like the catalog.
    assert [call[1] for call in account.calls].count("/v1/config") == 1


@pytest.mark.parametrize("cap", [None, 0, len(START) - 1, "65536"], ids=["older_worker", "zero", "too_small", "not_int"])
def test_the_ghost_goes_alone_when_the_service_takes_no_start_of_that_size(tmp_path, monkeypatch, cap):
    monkeypatch.setattr(ghost_start, "start_for_upload", lambda path, digest: START)
    raw, source = _source(tmp_path)
    account = Service(start_cap=cap)
    _offer(tmp_path, account, [source])
    admissions, puts = _sent(account)
    assert set(admissions[0]["json"]) == UPLOAD_FIELDS
    assert puts[0]["content"] == codec.encode(raw)


@pytest.mark.parametrize("given", [None, b"", "text", bytearray(START), b"x" * (ghost_start.MAX_BYTES + 1)],
                         ids=["none", "empty", "str", "bytearray", "oversize"])
def test_no_usable_start_sends_the_unchanged_upload_and_reads_no_config(tmp_path, monkeypatch, given):
    monkeypatch.setattr(ghost_start, "start_for_upload", lambda path, digest: given)
    raw, source = _source(tmp_path)
    account = Service()
    _offer(tmp_path, account, [source])
    admissions, puts = _sent(account)
    assert set(admissions[0]["json"]) == UPLOAD_FIELDS and puts[0]["content"] == codec.encode(raw)
    assert "/v1/config" not in [call[1] for call in account.calls]


def test_a_grant_key_binds_whether_the_start_rides_along(tmp_path, monkeypatch):
    """A retry after the Worker learns starts sends a new body, so it needs a new
    key; the same body keeps its key."""
    monkeypatch.setattr(ghost_start, "start_for_upload", lambda path, digest: START)
    _, source = _source(tmp_path)
    account = Service(start_cap=None)
    population = CachedPopulation(Local(), root=tmp_path / "cache", account=account)
    sync = CommunitySync(account, population)
    async def scenario():
        await sync._offer(source)
        alone = dict(source["mutation_keys"])
        await sync._offer(source)
        assert source["mutation_keys"] == alone
        account.start_cap, sync._start_until = 64 * 1024, 0
        await sync._offer(source)
        assert source["mutation_keys"]["grant"] != alone["grant"]
        assert len(source["mutation_generation"]) <= 128
    try:
        asyncio.run(scenario())
    finally:
        population.close()
    admissions, _ = _sent(account)
    assert [("start_sha256" in call["json"]) for call in admissions] == [False, False, True]


def _download(tmp_path, account, identifier="1" * 64):
    population = CachedPopulation(Local(), root=tmp_path / "cache", account=account)
    sync = CommunitySync(account, population)
    asyncio.run(sync._asset(identifier))
    return population


def test_a_downloaded_start_is_kept_with_the_ghost_and_handed_to_its_owner(tmp_path, monkeypatch):
    handed = []
    monkeypatch.setattr(ghost_start, "accept_download", lambda *args: handed.append(args))
    account = Service(served_start=START)
    population = _download(tmp_path, account)
    try:
        identifier = "1" * 64
        assert population.is_ready(identifier)
        assert population.start_of(identifier) == START
        assert (tmp_path / "cache" / (identifier + ".start")).read_bytes() == START
        assert [args[:3] for args in handed] == [(identifier, recording()["raw_sha256"], START)]
        assert handed[0][3] == native()                    # the ghost's own bytes: what the start is bound to
        start_call = next(call for call in account.calls if call[1] == f"/v1/recordings/{identifier}/start")
        assert start_call[2]["limit"] == len(START)
    finally:
        population.close()


def test_a_ghost_without_a_start_makes_no_extra_request(tmp_path, monkeypatch):
    handed = []
    monkeypatch.setattr(ghost_start, "accept_download", lambda *args: handed.append(args))
    account = Service()
    population = _download(tmp_path, account)
    try:
        assert population.is_ready("1" * 64) and population.start_of("1" * 64) is None
        assert not any(call[1].endswith("/start") for call in account.calls) and not handed
    finally:
        population.close()


@pytest.mark.parametrize("trouble", ["refused", "wrong_bytes", "owner_raises"])
def test_a_bad_start_never_costs_the_ghost(tmp_path, monkeypatch, trouble):
    def owner(*args):
        raise ValueError("refused by its owner")
    monkeypatch.setattr(ghost_start, "accept_download", owner if trouble == "owner_raises" else lambda *args: None)
    account = Service(served_start=START)
    if trouble == "refused":
        account.start_failure = CommunityError("request_failed", status=404)
    population = CachedPopulation(Local(), root=tmp_path / "cache", account=account)
    sync = CommunitySync(account, population)
    if trouble == "wrong_bytes":
        original = account.request_bytes
        async def tampered(method, path, **kwargs):
            data = await original(method, path, **kwargs)
            return bytes([data[0] ^ 1]) + data[1:] if path.endswith("/start") else data
        account.request_bytes = tampered
    try:
        asyncio.run(sync._asset("1" * 64))
        assert population.is_ready("1" * 64)
        assert (population.start_of("1" * 64) is None) == (trouble != "owner_raises")
    finally:
        population.close()


@pytest.mark.parametrize("headers", [
    {"ghost-start-sha256": START_SHA},
    {"ghost-start-bytes": "26112"},
    {"ghost-start-sha256": START_SHA.upper(), "ghost-start-bytes": "26112"},
    {"ghost-start-sha256": START_SHA, "ghost-start-bytes": "-1"},
    {"ghost-start-sha256": START_SHA, "ghost-start-bytes": "0"},
    {"ghost-start-sha256": START_SHA, "ghost-start-bytes": str(ghost_start.MAX_BYTES + 1)},
    {"ghost-start-sha256": START_SHA, "ghost-start-bytes": "２６"},
])
def test_a_malformed_start_header_asks_for_nothing(tmp_path, headers):
    account = Service()
    original = account.request_bytes
    async def named(method, path, **kwargs):
        data = await original(method, path, **kwargs)
        if kwargs.get("response_headers") is not None:
            kwargs["response_headers"].update(headers)
        return data
    account.request_bytes = named
    population = _download(tmp_path, account)
    try:
        assert population.is_ready("1" * 64)
        assert not any(call[1].endswith("/start") for call in account.calls)
    finally:
        population.close()


def test_the_cache_counts_a_start_in_its_byte_cap_and_evicts_it_with_its_ghost(tmp_path, monkeypatch):
    account = Account()
    population = CachedPopulation(Local(), root=tmp_path, account=account)
    first, second = recording("1" * 64), recording("2" * 64)
    encoded = codec.encode(native())
    # Two ghosts fit by themselves; their starts push the cache over its cap.
    monkeypatch.setattr(cache, "CACHE_ENCODED", 2 * len(encoded) + len(START))
    try:
        assert population.store_asset(first, encoded, native(), start=START)
        assert population.store_asset(second, encoded, native(), start=START)
        assert not (tmp_path / ("1" * 64 + ".start")).exists() and not (tmp_path / ("1" * 64 + ".ghost")).exists()
        assert population.start_of("2" * 64) == START
        # A later copy without a start leaves no stale one behind.
        assert population.store_asset(second, encoded, native())
        assert population.start_of("2" * 64) is None and not (tmp_path / ("2" * 64 + ".start")).exists()
    finally:
        population.close()


def test_settings_ghost_files_count_and_delete_a_kept_start_with_its_ghost(tmp_path):
    population = CachedPopulation(Local(), root=tmp_path, account=Account())
    try:
        encoded = codec.encode(native())
        population.store_asset(recording(), encoded, native(), start=START)
        held = population.cached_holdings()
        assert held[0]["bytes"] == len(encoded) + len(native()) + len(START)
        assert population.drop_cached(["1" * 64])["bytes"] == held[0]["bytes"]
        assert not list(tmp_path.glob("1" * 64 + ".*"))
    finally:
        population.close()


def test_a_kept_start_that_changed_on_disk_is_not_handed_out(tmp_path):
    population = CachedPopulation(Local(), root=tmp_path, account=Account())
    try:
        population.store_asset(recording(), codec.encode(native()), native(), start=START)
        (tmp_path / ("1" * 64 + ".start")).write_bytes(bytes([START[0] ^ 1]) + START[1:])
        assert population.start_of("1" * 64) is None
    finally:
        population.close()


def test_a_restart_removes_kept_starts_with_their_ghosts(tmp_path):
    population = CachedPopulation(Local(), root=tmp_path, account=Account())
    population.store_asset(recording(), codec.encode(native()), native(), start=START)
    population.close()
    assert (tmp_path / ("1" * 64 + ".start")).exists()
    CachedPopulation(Local(), root=tmp_path, account=Account()).close()
    assert not list(tmp_path.glob("*.start"))


def test_a_byte_request_can_hand_back_its_response_headers():
    async def scenario():
        def respond(request):
            return httpx.Response(200, headers={"Ghost-Start-SHA256": START_SHA, "Content-Type": "application/octet-stream"},
                                  content=b"ghost")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            headers = {}
            assert await account.request_bytes("GET", "/v1/config", authenticated=False, response_headers=headers) == b"ghost"
            assert headers["ghost-start-sha256"] == START_SHA
    asyncio.run(scenario())


# -- the seam's owner calls (ghost/start_frame.py), not stubs ------------------------------------------

def test_upload_asks_the_owner_for_his_own_start(tmp_path, monkeypatch):
    from sm64_events.ghost import start_frame
    monkeypatch.setattr("sm64_events.core.paths.ghost_population_root", lambda: tmp_path)
    raw = native()
    digest = hashlib.sha256(raw).hexdigest()
    assert ghost_start.start_for_upload(tmp_path / "x.ghost", digest) is None          # no start: the ghost goes alone
    asked = []
    monkeypatch.setattr(start_frame, "start_blob", lambda root, sha: asked.append((root, sha)) or b"start")
    assert ghost_start.start_for_upload(tmp_path / "x.ghost", digest) == b"start" and asked == [(tmp_path, digest)]


def test_download_hands_the_owner_the_ghosts_own_bytes(tmp_path, monkeypatch):
    from sm64_events.ghost import start_frame
    monkeypatch.setattr("sm64_events.core.paths.ghost_population_root", lambda: tmp_path)
    handed = []
    monkeypatch.setattr(start_frame, "accept_start_blob", lambda root, raw, blob: handed.append((root, raw, blob)))
    raw = native()
    digest = hashlib.sha256(raw).hexdigest()
    ghost_start.accept_download("1" * 64, digest, START, raw)
    assert handed == [(tmp_path, raw, START)]
    assert ghost_start.accept_download("1" * 64, digest, START) is None and len(handed) == 1   # nothing to bind it to
    with pytest.raises(start_frame.StartError, match="other ghost bytes"):
        ghost_start.accept_download("1" * 64, "0" * 64, START, raw)


def test_a_start_its_owner_refuses_never_costs_the_ghost_end_to_end(tmp_path, monkeypatch):
    """The real owner refuses a blob that is no start at all; the ghost is still kept and playable."""
    monkeypatch.setattr("sm64_events.core.paths.ghost_population_root", lambda: tmp_path / "population")
    account = Service(served_start=START)                     # 26 KB of counting bytes: not a start
    population = _download(tmp_path, account)
    try:
        assert population.is_ready("1" * 64)
        assert not (tmp_path / "population" / "starts").exists()
    finally:
        population.close()

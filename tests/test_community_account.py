import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from sm64_events.community import CommunityAccount, CommunityConfig, CommunityError, RefreshTokenStore

UID = "11111111-1111-4111-8111-111111111111"


class Store:
    def __init__(self):
        self.token = None
        self.saved = []
    def load(self):
        return self.token
    def save(self, value):
        self.token = value
        self.saved.append(value)
    def clear(self):
        self.token = None


def config(**kwargs):
    return CommunityConfig(public_url="https://ghosts.example", supabase_url="https://project.supabase.co",
        publishable_key="public", oauth_client_id="native-client", enabled=True, **kwargs)


def profile(**kwargs):
    return {"v": 1, "uid": UID, "name": "Runner", "status": "active", "sharing": False,
        "terms_version": "t1", "privacy_version": "p1", "sheet_runner": None, "sheet_verified": False, **kwargs}


def token(refresh="rotated"):
    return {"access_token": "private-access", "refresh_token": refresh, "token_type": "Bearer", "expires_in": 3600}


def test_config_and_disabled_snapshot_need_no_vault_or_network():
    account = CommunityAccount(CommunityConfig(), token_store=Store())
    assert account.snapshot()["enabled"] is False
    assert asyncio.run(account.restore())["status"] == "signed_out"
    with pytest.raises(CommunityError, match="unavailable"):
        asyncio.run(account.begin_sign_in("google"))
    asyncio.run(account.close())
    with pytest.raises(ValueError):
        config(callback_url="http://127.0.0.1:8070/callback")
    with pytest.raises(ValueError):
        CommunityConfig(public_url="http://example.com", supabase_url="https://example.com", enabled=True,
                        publishable_key="public", oauth_client_id="client")


def test_policy_rejection_refreshes_current_versions_once_before_renewal():
    async def scenario():
        versions = ['t1', 't2']
        reads = []
        def respond(request):
            if request.url.path == '/v1/config':
                reads.append(request.url.path)
                return httpx.Response(200, json={'v': 1, 'terms_version': versions.pop(0),
                    'privacy_version': 'p1', 'terms_url': 'https://ghosts.example/terms'})
            if request.url.path == '/v1/pool':
                return httpx.Response(403, json={'v': 1, 'error': {'code': 'policy_required'}})
            return httpx.Response(200, json=profile())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = 'fixture', 10**20
            assert (await account.refresh())['status'] == 'active'
            with pytest.raises(CommunityError):
                await account.request_json('GET', '/v1/pool')
            assert account.snapshot()['status'] == 'consent_required'
            assert (await account.refresh())['terms_version_current'] == 't2'
            await account.refresh()
            assert len(reads) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["discord", "google", "twitch", "email"])
def test_pkce_portal_code_exchange_one_use_and_profile_defaults(provider):
    async def scenario():
        requests = []
        def respond(request):
            requests.append(request)
            if request.url.path == "/v1/config":
                return httpx.Response(200, json={"v": 1, "terms_version": "t1", "privacy_version": "p1",
                    "terms_url": "https://ghosts.example/policies#terms", "privacy_url": "https://ghosts.example/policies#privacy"})
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json=token())
            return httpx.Response(200, json=profile())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            store = Store()
            account = CommunityAccount(config(), http=http, token_store=store)
            begin = await account.begin_sign_in(provider)
            outer = parse_qs(urlsplit(begin["url"]).query)
            assert urlsplit(begin["url"]).path == "/auth/start"
            assert outer["provider"] == [provider]
            native_url = outer["native_authorize"][0]
            params = parse_qs(urlsplit(native_url).query)
            assert params["code_challenge_method"] == ["S256"]
            assert params["response_type"] == ["code"]
            assert "access_token" not in native_url and "refresh_token" not in native_url
            with pytest.raises(CommunityError):
                await account.complete_sign_in(begin['attempt_id'], 'code', 'wrong-state')
            signed = await account.complete_sign_in(begin["attempt_id"], "code", params["state"][0])
            assert signed["uid"] == UID and signed["provider"] == provider
            assert "private-access" not in repr(signed) and "rotated" not in repr(signed)
            post = next(request for request in requests if request.url.path.endswith("/token"))
            body = parse_qs(post.content.decode())
            assert body["client_id"] == ["native-client"] and "client_secret" not in body
            assert len(body["code_verifier"][0]) >= 43
            assert store.saved == ["rotated"]
            with pytest.raises(CommunityError):
                await account.complete_sign_in(begin["attempt_id"], "code", params["state"][0])
            await account.complete_profile("Runner", terms_version="t1", privacy_version="p1")
            import json
            put = next(request for request in requests if request.method == "PUT")
            assert json.loads(put.content)["sharing"] is False
            assert json.loads(put.content)["sheet_runner"] is None
            assert account.policy_snapshot()["terms_version_current"] == "t1"
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["wrong", "canceled", "expired"])
def test_callback_refusal_happens_before_token_request(case):
    async def scenario():
        count = []
        def respond(request):
            count.append(request.url.path)
            return httpx.Response(200, json={"v": 1})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            begin = await account.begin_sign_in("discord")
            state = account._attempt[1]
            if case == "canceled":
                account.cancel_sign_in(begin["attempt_id"])
            elif case == "expired":
                pending = list(account._attempt)
                pending[3] = 0
                account._attempt = tuple(pending)
            with pytest.raises(CommunityError):
                await account.complete_sign_in(begin["attempt_id"], "code", "wrong" if case == "wrong" else state)
            assert count == ["/v1/config"]
    asyncio.run(scenario())


def test_cancel_inflight_exchange_cannot_store_credentials():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(request):
            if request.url.path.endswith("/token"):
                entered.set()
                await release.wait()
                return httpx.Response(200, json=token())
            return httpx.Response(200, json={"v": 1})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            store = Store()
            account = CommunityAccount(config(), http=http, token_store=store)
            begin = await account.begin_sign_in("discord")
            state = account._attempt[1]
            task = asyncio.create_task(account.complete_sign_in(begin["attempt_id"], "code", state))
            await asyncio.wait_for(entered.wait(), 2)
            account.cancel_sign_in(begin["attempt_id"])
            release.set()
            with pytest.raises(CommunityError):
                await task
            assert not store.saved and not account.snapshot()["authenticated"]
    asyncio.run(scenario())


def test_restore_rotates_and_store_failure_does_not_install_access():
    async def scenario():
        def respond(request):
            return httpx.Response(200, json=token("next") if request.url.path.endswith("/token") else
                                  profile() if request.url.path == "/v1/account" else {"v": 1})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            store = Store()
            store.token = "old"
            account = CommunityAccount(config(), http=http, token_store=store)
            assert (await account.restore())["authenticated"]
            assert store.saved == ["next"]
            def refuse(value):
                raise CommunityError("credential_store_unavailable")
            store.save = refuse
            account._expires = 0
            with pytest.raises(CommunityError):
                await account.refresh()
            assert not account.snapshot()["authenticated"]
    asyncio.run(scenario())


def test_credential_store_scopes_and_no_plaintext_fallback():
    class API:
        def __init__(self):
            self.rows = {}
        def read(self, name):
            return self.rows.get(name)
        def write(self, name, token):
            self.rows[name] = token
        def delete(self, name):
            self.rows.pop(name, None)
    api = API()
    first = RefreshTokenStore(config(), api=api)
    other = RefreshTokenStore(config(channel="other"), api=api)
    first.save("private-refresh")
    assert first.load() == "private-refresh" and other.load() is None
    assert "private-refresh" not in first.target
    first.clear()
    assert first.load() is None


def test_http_failure_is_bounded_and_redacted():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500,
            json={"error": {"code": "private-access", "message": "private-access"}}))) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "private-access", 10**20
            with pytest.raises(CommunityError) as found:
                await account.refresh()
            assert "private-access" not in str(found.value) and found.value.code == "request_failed"
    asyncio.run(scenario())


def test_authlib_injected_transport_uses_public_client_form():
    from sm64_events.community.account import _OAuth
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=token()))) as http:
            async with _OAuth(config(), http) as oauth:
                result = await oauth.fetch_token("https://project.supabase.co/auth/v1/oauth/token", code="fixture", code_verifier="x" * 64)
                assert result["token_type"] == "Bearer"
    asyncio.run(scenario())


def test_old_account_response_cannot_restore_signed_out_profile():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(request):
            entered.set()
            await release.wait()
            return httpx.Response(200, json=profile())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            task = asyncio.create_task(account.refresh())
            await asyncio.wait_for(entered.wait(), 2)
            await account.sign_out()
            release.set()
            with pytest.raises(CommunityError):
                await task
            assert account.snapshot()["status"] == "signed_out"
    asyncio.run(scenario())


def test_short_lived_exchange_rotates_without_recursive_auth_lock():
    async def scenario():
        tokens = []
        def respond(request):
            if request.url.path.endswith("/token"):
                value = token()
                if not tokens:
                    value["expires_in"] = 1
                tokens.append(value)
                return httpx.Response(200, json=value)
            return httpx.Response(200, json=profile() if request.url.path == "/v1/account" else {"v": 1})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            begin = await account.begin_sign_in("email")
            state = account._attempt[1]
            result = await asyncio.wait_for(account.complete_sign_in(begin["attempt_id"], "fixture", state), 2)
            assert result["provider"] == "email" and len(tokens) == 2
            assert account._inflight is None
    asyncio.run(scenario())


def test_mutation_keys_are_uuid_per_operation_and_explicit_replay_is_stable():
    from uuid import UUID
    async def scenario():
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json=profile())
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            await account.rename("One")
            await account.rename("Two")
            first, second = [request.headers["idempotency-key"] for request in requests]
            assert str(UUID(first)) == first and first != second
            explicit = "22222222-2222-4222-8222-222222222222"
            for _ in range(2):
                await account.request_json("PUT", "/v1/account/name", json={"name": "Replay"},
                                           headers={"idempotency-key": explicit})
            assert [request.headers["idempotency-key"] for request in requests[-2:]] == [explicit, explicit]
    asyncio.run(scenario())


@pytest.mark.parametrize("code,status", [("rename_required", "rename_required"), ("policy_required", "consent_required"),
    ("consent_required", "consent_required"), ("suspended", "suspended"), ("deleted", "deleted")])
def test_authenticated_refusal_updates_cached_account_gate(code, status):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                httpx.Response(403, json={"v": 1, "error": {"code": code, "message": "remote input"}}))) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            account._profile = {key: value for key, value in profile().items() if key != "v"}
            with pytest.raises(CommunityError):
                await account.request_json("GET", "/v1/catalog")
            assert account.snapshot()["status"] == status
            assert "remote input" not in repr(account.snapshot())
    asyncio.run(scenario())


def test_policy_refresh_is_throttled_and_new_versions_require_consent():
    async def scenario():
        calls = []
        def respond(request):
            calls.append(request.url.path)
            return httpx.Response(200, json=profile() if request.url.path == "/v1/account" else
                {"v": 1, "terms_version": "t2", "privacy_version": "p1", "terms_url": "https://ghosts.example/policies#terms"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            assert (await account.refresh())["status"] == "consent_required"
            assert (await account.refresh())["status"] == "consent_required"
            assert calls == ["/v1/config", "/v1/account", "/v1/account"]
            account._policies_until = 0
            await account.refresh()
            assert calls.count("/v1/config") == 2
    asyncio.run(scenario())

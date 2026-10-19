"""Protocol-v2 additions use the real bounded account HTTP transport."""
import asyncio
import json

import httpx
import pytest

from sm64_events.community import CommunityAccount, CommunityError
from test_community_account import Store, config


@pytest.mark.parametrize('path,version', [('/v1/account', 1), ('/v1/catalog', 1), ('/v2/account/progress', 2),
                                       ('/v2/players/10000000-0000-4000-8000-000000000001', 2)])
def test_account_transport_accepts_each_routes_exact_protocol_version_without_credentials(path, version):
    async def scenario():
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={'v': version, 'ok': True})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            assert await account.request_json('GET', path, authenticated=False) == {'v': version, 'ok': True}
            assert len(calls) == 1 and 'authorization' not in calls[0].headers
    asyncio.run(scenario())


@pytest.mark.parametrize('path,version', [('/v1/account', 2), ('/v2/account/progress', 1),
                                       ('/v1/account', True), ('/v2/account/progress', '2'), ('/v2/account/progress', None)])
def test_wrong_protocol_version_is_refused(path, version):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={'v': version}))) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            with pytest.raises(CommunityError) as error: await account.request_json('GET', path, authenticated=False)
            assert error.value.code == 'invalid_response'
    asyncio.run(scenario())


@pytest.mark.parametrize('method,path,padding,allowed', [
    ('GET', '/v1/catalog', 2 << 20, True),
    ('GET', '/v1/account/export', 1100000, False),
    ('GET', '/v2/players/10000000-0000-4000-8000-000000000001/progress', 1100000, False),
    ('GET', '/v2/account/player-export', 1100000, True),
    ('GET', '/v2/account/player-export', (2 << 20) + 1, False),
    ('POST', '/v2/account/player-export', 1100000, False),
    ('GET', '/v2/account/player-export/extra', 1100000, False),
])
def test_only_exact_owner_export_get_receives_two_mib_exception_and_v1_catalog_retains_its_bound(method, path, padding, allowed):
    async def scenario():
        version = 2 if path.startswith('/v2/') else 1
        encoded = json.dumps({'v': version, 'padding': 'x' * padding}).encode()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=encoded))) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            if allowed:
                assert (await account.request_json(method, path, authenticated=False))['v'] == version
            else:
                with pytest.raises(CommunityError) as error: await account.request_json(method, path, authenticated=False)
                assert error.value.code == 'response_too_large'
    asyncio.run(scenario())


@pytest.mark.parametrize('code,expected', [('publisher_conflict', 'publisher_conflict'), ('revision_conflict', 'revision_conflict'),
                                         ('progress_unavailable', 'progress_unavailable'), ('snapshot_too_large', 'snapshot_too_large'),
                                         ('private-secret-token', 'request_failed')])
def test_remote_error_codes_are_allowlisted_and_descriptions_never_escape(code, expected):
    async def scenario():
        def respond(_request):
            return httpx.Response(409, json={'v': 2, 'error': {'code': code, 'message': 'private-secret-remote-description'}},
                                  headers={'Retry-After': '7'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            with pytest.raises(CommunityError) as error: await account.request_json('GET', '/v2/account/progress', authenticated=False)
            assert error.value.code == expected and error.value.status == 409 and error.value.retry_after == 7
            assert 'private-secret' not in str(error.value) and 'private-secret' not in error.value.public_message
    asyncio.run(scenario())


@pytest.mark.parametrize('path', ['/v2/../account', '/v2/account?secret=bad', '/v2/account#token', '/v3/account'])
def test_invalid_route_is_rejected_before_http(path):
    async def scenario():
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={'v': 2})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            with pytest.raises(CommunityError) as error: await account.request_json('GET', path, authenticated=False)
            assert error.value.code == 'invalid_route' and not calls
    asyncio.run(scenario())

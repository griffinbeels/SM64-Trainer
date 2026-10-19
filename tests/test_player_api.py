"""Loopback player pages consume public facts through actual local resolvers."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from sm64_events.community.progress_snapshot import project
from sm64_events.ghost.sharing_targets import canonical
from sm64_events.server.player_api import create_player_router
from test_community_account import UID
from test_community_sync import recording
from test_progress_snapshot import SAVED, pb, service


class Progress:
    def __init__(self, owner):
        content = project(owner)
        self.account = SimpleNamespace(snapshot=lambda: {"uid": UID})
        self.header = {"v": 2, "uid": UID, "claimed_account": True, "name": "Actual player",
                       "bio": "", "socials": [], "sheet_verified": False, "sheet_runner": "Unproven same name",
                       "progress": {"available": True}}
        self.saved = {"v": 2, "uid": UID, "revision": 1, "content": content,
                      "content_hash": hashlib.sha256(canonical(content)).hexdigest(), "captured_utc": SAVED, "synced_utc": SAVED}
        self.header["progress"].update({key: self.saved[key] for key in ("revision", "content_hash", "captured_utc", "synced_utc")})
        self.search_value = {"v": 2, "players": [self.header], "complete": True, "next_cursor": None}
        self.recording_value = {"v": 2, "uid": UID, "generation": "g1", "recordings": [], "next_cursor": None, "complete": True}
        self.effects = []
    async def player(self, uid): return deepcopy(self.header)
    async def published(self, uid): return deepcopy(self.saved)
    async def search(self, prefix, cursor): return deepcopy(self.search_value)
    async def recordings(self, uid, cursor): return deepcopy(self.recording_value)
    async def status(self, **kwargs): return {"available": True}
    async def control(self, *args):
        self.effects.append(("control", args))
        return {"available": True}
    async def save_profile(self, body):
        self.effects.append(("details", body))
        return {"available": True}
    async def synchronize(self): self.effects.append(("sync",))


def client(owner=None, progress=True):
    owner = service([pb(frames=300), pb(2, star_id=6, frames=900)]) if owner is None else owner
    boundary = Progress(owner) if progress is True else progress
    app = FastAPI()
    app.include_router(create_player_router(owner, boundary))
    return TestClient(app, raise_server_exceptions=False), owner, boundary


def test_claimed_self_comparison_uses_real_readers_and_exact_zero_tiles_without_implying_run_verification():
    http, _owner, _boundary = client()
    with http:
        response = http.get('/api/community/players/' + UID)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['player']['uid'] == UID and value['player']['claimed']
    assert 'does not verify' in value['player']['claim_explanation'] and value['player']['sheet_runner'] is None
    comparison = value['comparison']
    assert comparison['card']['total']['delta_cs'] == 0
    assert all(tile['delta_cs'] == 0 for row in comparison['card']['rows'] for tile in row['tiles'] if tile['you_cs'] is not None)
    assert comparison['player_rank']['marelo'] == comparison['you_rank']['marelo']
    assert comparison['context']['basis'] == 'pb' and comparison['context']['default_rom'] == 'us'
    assert response.headers['cache-control'] == 'no-store' and response.headers['x-content-type-options'] == 'nosniff'


def test_selected_viewer_scope_and_exclusions_apply_to_both_columns():
    owner = service([pb(frames=300), pb(2, star_id=1, frames=600), pb(3, course_id=2, star_id=0, frames=900)])
    owner.rank_excluded = lambda: {'star:1:1'}
    http, _owner, _boundary = client(owner)
    with http:
        response = http.get('/api/community/players/' + UID, params={'scope': 'course:1'})
    assert response.status_code == 200, response.text
    comparison = response.json()['comparison']
    keys = {tile['key'] for row in comparison['card']['rows'] for tile in row['tiles']}
    assert 'star:1:0' in keys and 'star:1:1' not in keys and 'star:2:0' not in keys
    assert comparison['context']['scope_id'] == 'course:1' and comparison['card']['total']['delta_cs'] == 0


def test_faster_published_pb_and_slower_ghost_are_kept_separate():
    http, _owner, boundary = client()
    boundary.recording_value['recordings'] = [recording(frames=450)]
    with http:
        player = http.get('/api/community/players/' + UID).json()
        ghosts = http.get('/api/community/players/' + UID + '/recordings')
    assert ghosts.status_code == 200, ghosts.text
    tile = next(tile for row in player['comparison']['card']['rows'] for tile in row['tiles'] if tile['key'] == 'star:1:0')
    assert tile['goal_cs'] == 1000 and ghosts.json()['recordings'][0]['frames'] == 450
    assert not boundary.effects and 'video' not in ghosts.json()['recordings'][0]


@pytest.mark.parametrize('private', [True, False])
def test_partial_or_private_progress_never_presents_unknown_facts_as_zero_rank(private):
    http, _owner, boundary = client()
    if private:
        boundary.header['progress']['available'] = False
    else:
        boundary.saved['content']['coverage'].update(total_rows=3, withheld_rows=1, complete=False)
        boundary.saved['content_hash'] = hashlib.sha256(canonical(boundary.saved['content'])).hexdigest()
    with http:
        response = http.get('/api/community/players/' + UID)
    assert response.status_code == 200, response.text
    value = response.json()
    if private:
        assert value['comparison'] is None and not value['progress']['published']
    else:
        assert value['comparison']['player_rank'] is None
    assert value['partial_reason']


@pytest.mark.parametrize('missing', ['db', 'ranks'])
def test_public_page_remains_readable_when_local_comparison_is_unavailable(missing):
    http, owner, _boundary = client()
    setattr(owner, missing, None)
    with http:
        response = http.get('/api/community/players/' + UID)
    assert response.status_code == 200, response.text
    assert response.json()['comparison'] is None and response.json()['progress']['published']
    assert response.json()['partial_reason']


@pytest.mark.parametrize('change', ['header_uid', 'header_claim', 'snapshot_uid', 'hash', 'content'])
def test_identity_and_snapshot_integrity_fail_safely(change):
    http, _owner, boundary = client()
    if change == 'header_uid': boundary.header['uid'] = '10000000-0000-4000-8000-000000000002'
    elif change == 'header_claim': boundary.header['claimed_account'] = False
    elif change == 'snapshot_uid': boundary.saved['uid'] = '10000000-0000-4000-8000-000000000002'
    elif change == 'hash': boundary.saved['content_hash'] = 'f' * 64
    else: boundary.saved['content']['rows'][0]['frames'] = True
    with http:
        response = http.get('/api/community/players/' + UID)
    assert response.status_code == 503, response.text
    assert not boundary.effects


@pytest.mark.parametrize('change', ['header_type', 'progress_type', 'snapshot_type', 'missing_content', 'missing_revision', 'search_type', 'recordings_type'])
def test_malformed_remote_json_returns_safe_503_instead_of_500(change):
    http, _owner, boundary = client()
    path = '/api/community/players/' + UID
    if change == 'header_type': boundary.header = ['private-remote-sentinel']
    elif change == 'progress_type': boundary.header['progress'] = 'private-remote-sentinel'
    elif change == 'snapshot_type': boundary.saved = None
    elif change == 'missing_content': boundary.saved.pop('content')
    elif change == 'missing_revision': boundary.saved.pop('revision')
    elif change == 'search_type':
        boundary.search_value = ['private-remote-sentinel']
        path = '/api/community/players'
    else:
        boundary.recording_value = None
        path += '/recordings'
    with http:
        response = http.get(path)
    assert response.status_code == 503, response.text
    assert 'private-remote-sentinel' not in response.text and not boundary.effects


def test_unconfigured_session_reports_unavailable_and_cannot_mutate():
    http, _owner, _boundary = client(progress=None)
    with http:
        value = http.get('/api/community/progress')
        effect = http.post('/api/community/progress/sync')
    assert value.status_code == 200 and value.json()['available'] is False
    assert effect.status_code == 503


@pytest.mark.parametrize('path,body', [('/progress/control', {'action': 'enable', 'expected_revision': True}),
                                     ('/progress/control', {'action': 'erase', 'expected_revision': 1}),
                                     ('/progress/control', {'action': 'disable', 'expected_revision': 1, 'extra': 'wrong'}),
                                     ('/player-profile', {'expected_revision': 0, 'bio': '', 'socials': [{}] * 6})])
def test_invalid_commands_are_rejected_before_the_boundary_has_effects(path, body):
    http, _owner, boundary = client()
    with http:
        response = http.put('/api/community' + path, json=body) if path == '/player-profile' else http.post('/api/community' + path, json=body)
    assert response.status_code == 422 and not boundary.effects


@pytest.mark.parametrize('change', ['owner', 'same_owner_epoch', 'second_uid'])
def test_combined_export_never_mixes_accounts_across_its_two_reads(change):
    from sm64_events.server.community_api import create_community_router

    class Account:
        _epoch = 0
        owner = UID

        def snapshot(self):
            return {'enabled': True, 'uid': self.owner, 'status': 'active'}

        async def request_json(self, _method, path):
            if path == '/v1/account/export':
                result = {'v': 1, 'account': {'uid': UID}, 'recordings': [], 'complete': True}
                if change == 'owner':
                    self.owner = '10000000-0000-4000-8000-000000000002'
                elif change == 'same_owner_epoch':
                    self._epoch += 1
                return result
            return {'v': 2, 'uid': '10000000-0000-4000-8000-000000000002'}

    app = FastAPI()
    app.include_router(create_community_router(Account()))
    with TestClient(app) as http:
        response = http.get('/api/community/account/export')
    assert response.status_code == (503 if change == 'second_uid' else 409)
    assert 'recordings' not in response.json() and 'player_page' not in response.json()


@pytest.mark.parametrize('broken_observer', [False, True])
def test_publication_invalidation_follows_completed_local_mutations_and_cannot_fail_them(broken_observer):
    from sm64_events.server.app import _install_progress_invalidation

    seen = []

    def invalidate():
        seen.append('invalidate')
        if broken_observer:
            raise RuntimeError('unavailable publication state')

    app = FastAPI()
    _install_progress_invalidation(app, SimpleNamespace(invalidate=invalidate))

    @app.post('/api/import/fixture')
    async def completed():
        seen.append('PB inserted')
        return {'imported': 1}

    with TestClient(app) as http:
        response = http.post('/api/import/fixture')
    assert response.status_code == 200 and response.json() == {'imported': 1}
    assert seen == ['PB inserted', 'invalidate']

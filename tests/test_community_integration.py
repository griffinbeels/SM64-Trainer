"""Entry-point contracts around community IO; all data and accounts are fixtures."""
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_ghost_adaptive import IDENTITY, Standards, recording
from test_ghost_population import recorded
from sm64_events.ghost.adaptive import checked_options, rank_frames, rank_recordings
from sm64_events.ghost.community_catalog import match_windows, model_cutoffs
from sm64_events.ghost.community_rivals import CommunityRivals
from sm64_events.ghost.population import LocalPopulation
from sm64_events.ghost.rival_recording import PopulationRecorder
from sm64_events.server.community_api import create_community_router
from sm64_events.server.local_access import HEADER, OAuthAccessFilter, mount_local_access


class Account:
    def __init__(self):
        self.status = 'signed_out'
        self.cancelled = []
        self.pending = None

    def snapshot(self):
        return {'enabled': True, 'authenticated': self.status != 'signed_out', 'status': self.status}

    async def begin_sign_in(self, provider):
        self.pending = 'attempt', 'expected-state'
        return {'attempt_id': 'attempt', 'url': 'https://community.example/auth/start'}

    def cancel_sign_in(self, identifier):
        self.cancelled.append(identifier)
        self.pending = None

    async def complete_sign_in(self, identifier, code, state):
        if self.pending != (identifier, state):
            raise ValueError('Sensitive provider details must not be forwarded.')
        self.pending = None
        self.status = 'new'

    async def check_name(self, name):
        from sm64_events.community.names import checked_name
        name = checked_name(name)
        return {'name': name, 'available': name.lower() != 'taken'}


@pytest.mark.parametrize("provider", ["discord", "twitch"])
def test_hosted_oauth_return_reaches_state_validation_without_opening_other_routes(provider):
    account = Account()
    app = FastAPI()
    app.include_router(create_community_router(account))
    mount_local_access(app, port=8072)
    navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
    with TestClient(app, base_url='http://127.0.0.1:8072') as client:
        token = client.get('/api/local-access').json()['request_token']
        assert client.post('/api/community/auth/start', json={'provider': provider}, headers={HEADER: token}).status_code == 200
        assert client.post('/api/community/auth/start', json={'provider': 'unregistered'}, headers={HEADER: token}).status_code == 422
        assert client.get('/api/local-access', headers=navigation).status_code == 403
        wrong = client.get('/api/community/auth/callback?code=one&state=wrong', headers=navigation)
        assert wrong.status_code == 400 and 'Sensitive' not in wrong.text
        assert account.status == 'signed_out' and account.pending
        assert client.get('/api/community/auth/callback?code=one&state=expected-state', headers={**navigation, 'Host': 'attacker.example:8072'}).status_code == 403
        result = client.get('/api/community/auth/callback?code=one&state=expected-state', headers=navigation)
        assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
        assert 'expected-state' not in result.text and account.status == 'new'
        assert client.get('/api/community/auth/callback?code=one&state=expected-state', headers=navigation).status_code == 400


def test_oauth_access_log_drops_return_query_before_formatting():
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s - "%s %s HTTP/%s" %d',
                               ('loopback', 'GET', '/api/community/auth/callback?code=secret-code&state=private-state', '1.1', 200), None)
    assert OAuthAccessFilter().filter(record)
    assert 'secret-code' not in record.getMessage() and 'private-state' not in record.getMessage()


@pytest.mark.parametrize('scope', ['strategy', 'overall'])
def test_catalog_bins_have_the_same_integer_boundaries_as_native_rank_selection(scope):
    ranks = Standards()
    identity = replace(IDENTITY, scope=scope)
    model = ranks.overall_curve('star:2:1', 'us') if scope == 'overall' else ranks.ladder_cs('star:2:1', 'Standard', 'us')
    bins = model_cutoffs(model, scope)
    for row in bins:
        assert rank_frames(ranks, identity, row['frames'])['step'] >= row['step']
        if row['frames'] < 100000:
            assert rank_frames(ranks, identity, row['frames'] + 1)['step'] < row['step']
    pb = {**rank_frames(ranks, identity, 600), 'frames': 600}
    windows = match_windows(ranks, identity, pb, checked_options({}))
    assert any(low <= 600 <= high for low, high in windows)


def test_remote_structural_validation_preserves_unverified_provenance():
    row = recording('remote', certified=False, provenance='community-unverified', structurally_valid=True)
    ranked = rank_recordings([row], Standards(), IDENTITY)
    assert len(ranked) == 1 and ranked[0]['certified'] is False
    assert ranked[0]['provenance'] == 'community-unverified'
    assert not rank_recordings([{**row, 'structurally_valid': False}], Standards(), IDENTITY)


def test_downloads_do_not_displace_playable_rivals_or_hold_up_private_choices():
    class Sync:
        requested = None
        def request_assets(self, values): self.requested = values
    sync = Sync()
    population = SimpleNamespace(is_ready=lambda key: key == 'old')
    staging = CommunityRivals(sync, population)
    old = {'id': 'old', 'slot': 0, 'provenance': 'community-unverified'}
    new = {'id': 'new', 'slot': 0, 'provenance': 'community-unverified'}
    private = {'id': 'private', 'slot': 1, 'manual_only': True}
    assert staging.stage((new, private), (old,)) == (old, private)
    assert sync.requested == ('new',) and staging.downloading
    population.is_ready = lambda key: True
    assert staging.stage((new, private), (old,)) == (new, private)
    assert not staging.downloading


def test_cloud_offer_precedes_independent_local_winner_skip(tmp_path):
    run, replay, path = recorded(tmp_path)
    original = path.read_bytes()
    population = LocalPopulation(tmp_path / 'population')
    population.accept_verified(run, replay, player_id='local', player_name='You', clock='igt', game_version='us', full_start=True)
    class Database:
        def get_state(self, key, default): return 'local' if key == 'ghost_player_id' else default
    class Sync:
        offers = []
        def contribution_context(self): return {'owner': 'qualified-owner', 'consent_epoch': 0}
        def remember_source(self, value, *, owner, consent_epoch):
            assert owner == 'qualified-owner' and consent_epoch == 0
            self.offers.append(value)
    sync = Sync()
    recorder = PopulationRecorder(population, replay, SimpleNamespace(db=Database(), ranks=Standards()), lambda run: IDENTITY, community=sync)
    assert recorder._admit(run) is False
    assert len(sync.offers) == 1 and sync.offers[0]['frames'] == run.igt_frames
    assert path.read_bytes() == original
    population.close()


def test_offline_beta_is_explicit_and_partial_cloud_configuration_is_refused():
    from sm64_events.core.build_channel import parse_metadata
    value = {'schema': 1, 'channel': 'beta', 'build_id': 'local-test'}
    build = parse_metadata(value)
    assert build.channel == 'beta' and not build.community_url and not build.updates_enabled
    with pytest.raises(ValueError, match='all four'):
        parse_metadata({**value, 'community_url': 'https://community.example'})


def test_desktop_shell_sign_in_opens_the_default_browser_through_the_server(monkeypatch):
    """Round 39: the shell handed the UI's blank tab to Windows as an 'about' link."""
    from sm64_events.server import community_api
    opened = []
    monkeypatch.setattr(community_api, "open_in_browser", lambda url: opened.append(url) or True)
    account = Account()
    app = FastAPI()
    app.include_router(create_community_router(account))
    mount_local_access(app, port=8072)
    with TestClient(app, base_url='http://127.0.0.1:8072') as client:
        token = client.get('/api/local-access').json()['request_token']
        plain = client.post('/api/community/auth/start', json={'provider': 'discord'}, headers={HEADER: token})
        assert plain.status_code == 200 and plain.json()['opened'] is False and opened == []
        shell = client.post('/api/community/auth/start', json={'provider': 'discord', 'open_browser': True},
                            headers={HEADER: token})
        assert shell.status_code == 200 and shell.json()['opened'] is True
        assert opened == ['https://community.example/auth/start']

        def refuse(_url):
            raise OSError('no browser')
        monkeypatch.setattr(community_api, "open_in_browser", refuse)
        failed = client.post('/api/community/auth/start', json={'provider': 'twitch', 'open_browser': True},
                             headers={HEADER: token})
        # The attempt still starts; the modal's own link remains the fallback.
        assert failed.status_code == 200 and failed.json()['opened'] is False and failed.json()['attempt_id']


def test_name_check_route_answers_availability_for_the_signed_in_account():
    account = Account()
    account.status = 'new'
    app = FastAPI()
    app.include_router(create_community_router(account))
    mount_local_access(app, port=8072)
    with TestClient(app, base_url='http://127.0.0.1:8072') as client:
        token = client.get('/api/local-access').json()['request_token']
        free = client.get('/api/community/names/check', params={'name': 'Fresh'}, headers={HEADER: token})
        assert free.status_code == 200 and free.json() == {'name': 'Fresh', 'available': True}
        held = client.get('/api/community/names/check', params={'name': 'taken'}, headers={HEADER: token})
        assert held.status_code == 200 and held.json()['available'] is False
        assert client.get('/api/community/names/check', params={'name': 'abc'}, headers={HEADER: token}).status_code == 400
        assert client.get('/api/community/names/check', headers={HEADER: token}).status_code == 422


@pytest.mark.parametrize('scope', ['strategy', 'overall'])
def test_the_pool_request_covers_every_bin_so_the_range_can_widen(scope):
    """His rule (round 49): the rival list widens above the range, then below,
    until it is full, so the cloud pool must hold every bin; never the exact PB."""
    from sm64_events.community.sync import frame_band
    ranks = Standards()
    identity = replace(IDENTITY, scope=scope)
    model = ranks.overall_curve('star:2:1', 'us') if scope == 'overall' else ranks.ladder_cs('star:2:1', 'Standard', 'us')
    cutoffs = [(row['step'], row['frames']) for row in model_cutoffs(model, scope)]
    pb = {**rank_frames(ranks, identity, 600), 'frames': 600}
    for basis in (pb, None):
        windows = match_windows(ranks, identity, basis, checked_options({}))
        assert frame_band(cutoffs, windows) == (cutoffs[0][0], cutoffs[-1][0], cutoffs[0][0])
        assert all(600 not in window for window in windows)  # never the exact PB as a bound

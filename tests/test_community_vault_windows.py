"""The native vault API handles one uniquely owned synthetic test entry."""
import os
import secrets
from uuid import uuid4

import pytest

from sm64_events.community import CommunityConfig, RefreshTokenStore


@pytest.mark.skipif(os.name != 'nt', reason='Windows Credential Manager API')
def test_real_windows_vault_round_trip_removes_its_owned_entry():
    config = CommunityConfig(channel='vault-test-' + str(uuid4()))
    store = RefreshTokenStore(config)
    synthetic = secrets.token_urlsafe(40)
    try:
        assert store.load() is None
        store.save(synthetic)
        assert secrets.compare_digest(store.load(), synthetic)
    finally:
        store.clear()
    assert store.load() is None

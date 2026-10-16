"""No live listeners or process termination: beta takeover and local token transport."""
import io
import json
import urllib.error

import pytest

from sm64_events.core import build_channel
from sm64_events.desktop import single_instance as si


@pytest.fixture
def beta(monkeypatch):
    monkeypatch.setattr(build_channel, "current_build", lambda: build_channel.BuildChannel(channel="beta"))


def test_beta_foreign_listener_never_receives_shutdown_or_force(beta):
    calls = []
    assert not si.take_over(identity_check=lambda: False,
        shutdown=lambda: calls.append("shutdown"), force_kill=lambda: calls.append("kill"))
    assert calls == []


def test_beta_only_gracefully_closes_identified_beta_and_never_force_kills(beta):
    assert si.take_over(identity_check=lambda: True, shutdown=lambda: True, port_free=lambda: True)
    calls = []
    assert not si.take_over(identity_check=lambda: True, shutdown=lambda: False,
        force_kill=lambda: calls.append("kill"), port_free=lambda: False, graceful_wait_s=0)
    assert calls == []


def test_beta_force_path_itself_is_inert(beta, monkeypatch):
    monkeypatch.setattr(si, "listener_pid", lambda *a: pytest.fail("queried listener for termination"))
    monkeypatch.setattr(si, "_force_kill_pidfile", lambda: pytest.fail("read a termination pidfile"))
    si._force_kill_port_owner()


def test_beta_direct_pidfile_termination_path_is_inert(beta, monkeypatch):
    monkeypatch.setattr(si, "pidfile_path", lambda: pytest.fail("read a termination pidfile"))
    si._force_kill_pidfile()


class Response(io.BytesIO):
    status = 200


def test_native_shutdown_supplies_local_request_token(monkeypatch):
    seen = []
    fixture_token = "test-only-" * 3
    def open_request(request, timeout):
        if isinstance(request, str):
            seen.append(request)
            return Response(json.dumps({"request_token": fixture_token}).encode())
        seen.append(request)
        return Response(b"")
    monkeypatch.setattr(si.urllib.request, "urlopen", open_request)
    assert si._post_shutdown()
    assert seen[0].endswith("/api/local-access")
    assert seen[1].get_header("X-sm64-request-token") == fixture_token


@pytest.mark.parametrize("channel,allowed", [("stable", True), ("beta", False)])
def test_only_legacy_stable_can_fall_back_after_token_endpoint_404(monkeypatch, channel, allowed):
    monkeypatch.setattr(build_channel, "current_build", lambda: build_channel.BuildChannel(channel=channel))
    monkeypatch.setattr(si, "_matching_beta_server", lambda: True)
    posts = []
    def open_request(request, timeout):
        if isinstance(request, str):
            raise urllib.error.HTTPError(request, 404, "old stable endpoint", {}, None)
        posts.append(request)
        return Response(b"")
    monkeypatch.setattr(si.urllib.request, "urlopen", open_request)
    assert si._post_shutdown() is allowed
    assert bool(posts) is allowed


def test_beta_post_refuses_foreign_health_before_token_or_mutation(beta, monkeypatch):
    seen = []
    def open_request(request, timeout):
        seen.append(request)
        return Response(b'{"channel":"stable"}')
    monkeypatch.setattr(si.urllib.request, "urlopen", open_request)
    assert not si._post_shutdown()
    assert len(seen) == 1 and seen[0].endswith("/health")

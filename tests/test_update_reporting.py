"""Update failures carry useful evidence through the actual private SDK spool."""
import json
import time

import pytest
from sentry_sdk.envelope import Envelope

from sm64_events.core import error_reporting as reporting
from sm64_events.core.error_reporting_privacy import safe_event
from sm64_events.core.updater import UpdateService
from test_update_cycle import V1, V2, ZIP_URL, _install_v1, _range_http
from test_updater import _fake_release


@pytest.fixture
def reporter(tmp_path, monkeypatch):
    monkeypatch.setattr("sm64_events.core.error_reporting_transport.Delivery", lambda _dsn: lambda _payload: (False, 0))
    folder = tmp_path / "reports"
    reporting.configure(dsn="https://public@example.invalid/123", release="1.0.0+synthetic",
                        environment="test", directory=folder)
    yield folder
    reporting.shutdown()


@pytest.mark.parametrize("outage", [2, 99])
def test_first_retry_and_terminal_error_retain_update_context(reporter, tmp_path, outage):
    before = _fake_release(tmp_path / "r1", "v1.0.0", V1)
    routes = _fake_release(tmp_path / "r2", "v2.0.0", V2)
    root = _install_v1(tmp_path, before)
    inner = _range_http(routes)
    remaining = outage

    def interrupted(req):
        nonlocal remaining
        if req.full_url == ZIP_URL and remaining:
            remaining -= 1
            raise ConnectionResetError(10054, "reset at https://private.invalid/asset?token=private-token")
        return inner(req)

    service = UpdateService(current_version="1.0.0", http=interrupted,
                            exe_path=root / "SM64Trainer.exe", state_path=tmp_path / "state.json",
                            frozen=True, retry_delays=(0,))
    service.status()
    service._run_apply(service._cache, service._plan, service._manifest_text, lambda: None)
    expected = 1 if outage == 2 else 2
    deadline = time.monotonic() + 3
    while len(list(reporter.glob("*.envelope"))) < expected and time.monotonic() < deadline:
        time.sleep(0.01)
    events = [Envelope.deserialize(path.read_bytes()).get_event() for path in reporter.glob("*.envelope")]
    assert len(events) == expected
    assert sorted(event["extra"]["update"]["attempt"] for event in events) == list(range(1, expected + 1))
    for event in events:
        assert event["release"] == "1.0.0+synthetic"
        context = event["extra"]
        assert context["component"] == "updater" and context["operation"] == "apply"
        update = context["update"]
        assert update["current_version"] == "1.0.0" and update["target_version"] == "2.0.0"
        assert context["stage"] == "full_download" and update["attempts"] == 2
        assert update["download_bytes"] == service._plan.download_bytes
        assert context["error_type"] == "ConnectionResetError" and context["errno"] == 10054
        assert safe_event(event)["extra"] == context  # persisted/reloaded projection
        assert "private.invalid" not in json.dumps(event) and "private-token" not in json.dumps(event)
    if expected == 2:
        terminal = next(event for event in events if event["extra"]["update"]["attempt"] == 2)
        assert any("update attempt 1/2 failed at full_download" in crumb["message"]
                   for crumb in terminal["breadcrumbs"]["values"])
        assert (root / "SM64Trainer.exe").read_bytes() == V1["SM64Trainer.exe"]
    else:
        assert (root / "SM64Trainer.exe").read_bytes() == V2["SM64Trainer.exe"]


def test_opt_out_does_not_collect_update_failure(reporter, tmp_path):
    reporting.set_enabled(False)
    routes = _fake_release(tmp_path / "release", "v2.0.0", V2)
    root = _install_v1(tmp_path, _fake_release(tmp_path / "old", "v1.0.0", V1))
    inner = _range_http(routes)

    def failed(req):
        if req.full_url == ZIP_URL:
            raise ConnectionResetError("synthetic outage")
        return inner(req)

    service = UpdateService(current_version="1.0.0", http=failed, exe_path=root / "SM64Trainer.exe",
                            state_path=tmp_path / "state.json", frozen=True, retry_delays=(0,))
    service.status()
    service._run_apply(service._cache, service._plan, service._manifest_text, lambda: None)
    assert service.status()["state"] == "error"
    assert not list(reporter.glob("*.envelope"))


def test_update_context_rejects_unrecognized_or_private_fields():
    event = safe_event({"extra": {"update": {
        "current_version": "1.10.0-beta.27", "target_version": "C:/Users/PrivatePerson/file",
        "attempt": 1, "attempts": True, "download_bytes": -1,
        "url": "https://private.invalid/asset?token=secret", "headers": {"Authorization": "secret"},
    }}})
    assert event["extra"]["update"] == {"current_version": "1.10.0-beta.27", "attempt": 1}
    assert safe_event(event) == event

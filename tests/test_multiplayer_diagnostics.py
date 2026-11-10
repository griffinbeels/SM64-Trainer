"""Actual room failures through SDK envelopes and the shared export schema."""
import json
import time
from types import SimpleNamespace

import pytest

from sm64_events.core import error_reporting
from sm64_events.core.diagnostic_privacy import safe_snapshot
from sm64_events.core.diagnostic_snapshot import DiagnosticSnapshot
from sm64_events.multiplayer.diagnostics import RoomDiagnostics, safe_multiplayer
from sm64_events.multiplayer.room import RoomError, RoomService
from test_diagnostic_evidence import captured_event, reporter  # noqa: F401 - shared SDK fixture
from test_multiplayer_room import Channel, FREQUENCY, Receipt, Recorder, Transport, wait_for


def snapshot(room):
    return DiagnosticSnapshot(multiplayer=room.diagnostic_status,
        native=SimpleNamespace(snapshot=lambda _: {'available': False}, close=lambda: None))


def test_room_projection_is_fixed_idempotent_and_rejects_private_markers():
    raw = {'phase': 'error', 'operation': 'host', 'error': 'transport_failed', 'generation': 1,
        'members': 3, 'names': ['PrivatePlayer'], 'code': 'ET1-PrivateCode', 'invites': ['PrivateInvite'],
        'peers': {'playing': 1, 'PrivatePlayer': 2}, 'transport': {'sent': 4, 'payload': 'PrivatePayload'},
        'native': {'state': 'refused', 'native_reason': 5, 'presentation_reason': 'unsupported',
                   'room': 'PrivateRoom', 'receipt_owner': 'PrivateCapability'}}
    clean = safe_multiplayer(raw)
    assert clean == safe_multiplayer(clean)
    assert clean['native']['native_reason'] == 5 and clean['peers'] == {'playing': 1}
    assert 'Private' not in json.dumps(safe_snapshot({'multiplayer': raw}))
    for field in ('phase', 'operation', 'error'):
        assert field not in safe_multiplayer({field: 'PrivatePlayer'})


def test_actual_startup_failure_reports_once_without_provider_exception_text(reporter):
    def unavailable(_notify):
        raise RuntimeError('PrivatePlayer ET1-PrivateCode PrivateInvite PrivateCapability')
    room = RoomService(Recorder(), unavailable, qpc_frequency=lambda: FREQUENCY)
    source = snapshot(room)
    unbind = error_reporting.set_diagnostic_source(source.collect)
    try:
        with pytest.raises(RoomError):
            room.identity()
        event = captured_event(reporter)
        assert event['extra']['diagnostics']['multiplayer']['error'] == 'transport_unavailable'
        assert event['extra']['component'] == 'multiplayer'
        assert 'Private' not in json.dumps(event)
        for _ in range(3):
            with pytest.raises(RoomError):
                room.identity()
        time.sleep(.03)
        assert len(list(reporter.glob('*.envelope'))) == 1
        local = source.collect(local=True)
        assert local['multiplayer']['error'] == 'transport_unavailable'
        assert 'Private' not in json.dumps(local)
    finally:
        room.close(); unbind(); source.close()


@pytest.mark.parametrize('fault', ['provider', 'native'])
def test_admitted_room_reports_provider_and_native_failure_with_current_safe_evidence(reporter, fault):
    transports, channels = [], []
    def factory(notify):
        value = Transport(notify); transports.append(value); return value
    def channel(endpoint):
        value = Channel(endpoint); channels.append(value); return value
    room = RoomService(Recorder(), factory, qpc_frequency=lambda: FREQUENCY, channel_factory=channel)
    source = snapshot(room)
    unbind = error_reporting.set_diagnostic_source(source.collect)
    try:
        room.host()
        if fault == 'provider':
            transports[0].event('fatal', code='PrivateRawVendorError')
            expected = 'transport_failed'
        else:
            value = wait_for(lambda: channels and channels[0])
            value.write = lambda _view: Receipt(state='refused', native_reason=5)
            expected = 'native_receiver_exhausted'
        wait_for(lambda: room.diagnostic_status().get('error') == expected)
        event = captured_event(reporter)
        evidence = event['extra']['diagnostics']['multiplayer']
        assert evidence['error'] == expected
        if fault == 'native':
            assert evidence['native']['state'] == 'refused' and evidence['native']['native_reason'] == 5
            assert 'Restart Project64' in room.snapshot()['error']
        assert 'Private' not in json.dumps(event)
    finally:
        room.close(); unbind(); source.close()


def test_normal_waiting_and_optout_never_report_or_backfill_current_fault(reporter):
    reports = RoomDiagnostics()
    for reason in ('waiting', 'hidden', 'different_scene', 'setup_pending', 'ready', 'displayed'):
        reports.problem(reason, category='presentation')
    assert list(reporter.glob('*.envelope')) == []
    error_reporting.set_enabled(False)
    reports.problem('transport_failed')
    assert list(reporter.glob('*.envelope')) == []
    error_reporting.set_enabled(True)
    reports.problem('transport_failed')
    assert list(reporter.glob('*.envelope')) == []

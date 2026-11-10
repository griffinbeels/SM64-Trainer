"""Fixed room evidence, shared by cached observations and report projection.

No identity, payload, invitation, exception text or source capability belongs
here. Reprojection at every reporting boundary also rejects forged raw context.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import logging
import threading
import time

PHASES = frozenset({'idle', 'starting', 'hosting', 'joining', 'connected', 'reconnecting',
                    'leaving', 'error', 'closing', 'closed'})
OPERATIONS = frozenset({'identity', 'host', 'invite', 'join', 'decline', 'leave', 'kick', 'retry', 'retry_peer'})
PEER_STATES = frozenset({'playing', 'paused', 'waiting', 'connecting', 'reconnecting', 'disconnected', 'disconnecting'})
PRESENTATION = frozenset({'waiting', 'hidden', 'different_scene', 'unsupported', 'ineligible',
                         'setup_pending', 'gateway_refused', 'compositor_refused', 'ready', 'displayed', 'clean_recording'})
TRANSPORT_FAULTS = frozenset({'transport_unavailable', 'transport_timeout', 'transport_failed', 'transport_stopped',
    'transport_protocol_error', 'transport_consumer_failed', 'transport_write_failed', 'transport_event_overflow',
    'transport_namespace_exhausted', 'authentication_expiring', 'authentication_lost', 'authentication_timeout',
    'device_identity_failed', 'login_failed', 'sdk_callback_failed', 'operation_timeout'})
_FAULTS = TRANSPORT_FAULTS | frozenset({'room_timeout', 'room_operation_failed', 'room_observation_failed',
    'native_receiver_unavailable', 'native_receiver_cleanup_pending', 'native_clock_mismatch',
    'native_receiver_refused', 'native_receiver_exhausted', 'transport_cleanup_pending', 'source_clock_mismatch'})
_REASONS = _FAULTS | frozenset({'invitation_expired', 'invalid_identity', 'cannot_invite', 'cannot_invite_self',
    'cannot_remove_player', 'cannot_retry_player', 'already_in_room', 'invalid_room_action', 'room_closed', 'room_busy'})
_NATIVE_NUMBERS = frozenset({'native_reason', 'eligible_mask', 'selected_mask', 'displayed_mask',
                            'updates', 'refusals', 'drops', 'presentations', 'gl_major', 'gl_minor', 'ineligible_mask'})
_STATS = frozenset({'sent', 'received', 'rejected', 'skipped', 'data_queue_clears', 'sdk_result',
                    'outgoing_bytes', 'outgoing_packets', 'incoming_bytes', 'incoming_packets'})
log = logging.getLogger('sm64.multiplayer')


def _numbers(value, keys):
    return {key: item for key, item in value.items() if key in keys and type(item) is int and 0 <= item < 1 << 64}


def safe_multiplayer(value):
    """One bounded schema; unknown strings and nested fields never survive."""
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for key, choices in (('phase', PHASES), ('operation', OPERATIONS), ('error', _REASONS)):
        if type(value.get(key)) is str and value[key] in choices:
            result[key] = value[key]
    result.update(_numbers(value, {'generation', 'members'}))
    for key, choices, numbers in (('peers', {}, PEER_STATES), ('transport', {}, _STATS),
            ('native', {'state': {'pending', 'applied', 'refused'}, 'presentation_reason': PRESENTATION}, _NATIVE_NUMBERS)):
        raw = value.get(key)
        if not isinstance(raw, Mapping):
            continue
        row = _numbers(raw, numbers)
        for name, allowed in choices.items():
            if type(raw.get(name)) is str and raw[name] in allowed:
                row[name] = raw[name]
        result[key] = row
    return result


class RoomDiagnostics:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache, self._reported = {}, {}

    def observe(self, value):
        with self._lock:
            self._cache = safe_multiplayer(value)

    def snapshot(self):
        with self._lock:
            return deepcopy(self._cache)

    def problem(self, reason, *, category=None):
        if reason not in _FAULTS and reason not in {'unsupported', 'ineligible', 'gateway_refused', 'compositor_refused'}:
            return
        from sm64_events.core import error_reporting
        epoch = error_reporting.collection_epoch()
        category = category or ('native_receiver' if reason.startswith('native_') else
            'observation' if reason == 'room_observation_failed' else 'transport')
        if type(category) is not str or category not in {'native_receiver', 'observation', 'transport', 'presentation'}:
            return
        now = time.monotonic()
        _, prior_time = self._reported.get(category, (None, float('-inf')))
        if now - prior_time < 3600:
            return
        self._reported[category] = epoch, now
        log.warning('Friends failure: category=%s reason=%s', category, reason)
        if epoch is not None:
            error_reporting.capture_problem('Friends connection or display failed',
                context={'component': 'multiplayer', 'stage': category, 'reason': reason}, expected_generation=epoch)

"""Celebration watermarks belong to the calibration that established them.

A refresh publishes before its asynchronous notification runs. The first GET
of its revision must absorb that change itself; an older pinned GET may still
finish, but cannot alter current watermarks or announce a stale celebration.
"""
from contextlib import nullcontext

from sm64_events.ranks import scopes, scoring

_REVISIONS = "marelo_watermark_revisions"


def _update_lock(service):
    registry = getattr(service.ranks, "calibrations", None)
    return registry.update_lock if registry is not None else nullcontext()


def _current(service, scored=None):
    if not getattr(service.ranks, "is_current_read", True):
        return False
    return (scored is None or scored.get("calibration_revision") ==
            getattr(service.ranks, "calibration_revision", None))


def _absorb(service, scope_id, scored, revisions):
    watermarks = service.marelo_watermarks()
    if scored["tier"]:
        watermarks[scope_id] = int(scoring.progression_key(scored["tier"], scored["division"]))
    else:
        watermarks.pop(scope_id, None)
    revisions[scope_id] = scored.get("calibration_revision")
    service.db.set_state("marelo_watermarks", watermarks)
    service.db.set_state(_REVISIONS, revisions)


def celebration_for(service, scope_id, scored, active_scope):
    """Apply current GET side effects and return only an earned celebration."""
    with _update_lock(service):
        if not _current(service, scored):
            return None
        revisions = service.db.get_state(_REVISIONS, {})
        changed = revisions.get(scope_id) != scored.get("calibration_revision")
        if changed:
            _absorb(service, scope_id, scored, revisions)
        if not scored["tier"]:
            return None
        key = scoring.progression_key(scored["tier"], scored["division"])
        service.sync_watermark(scope_id, key)
        celebration = None
        if scope_id == active_scope:
            if service.note_active_scope(active_scope):
                service.absorb_watermark(scope_id, key)
            elif not changed:
                celebration = scopes.celebration_delta(
                    scored["tier"], scored["division"], service.marelo_watermarks().get(scope_id))
        service.seed_watermark(scope_id, key)
        return ({**celebration, "calibration_revision": scored.get("calibration_revision")}
                if celebration else None)


def absorb_regrade(service, score_scope):
    """Absorb explicit regrades, including imports that do not change a curve."""
    if service.db is None or service.ranks is None:
        return
    with _update_lock(service):
        if not _current(service):
            return
        revisions = service.db.get_state(_REVISIONS, {})
        for scope_id in list(service.marelo_watermarks()):
            try:
                scored = score_scope(service, scope_id)
            except (LookupError, ValueError):
                continue
            if not _current(service, scored):
                return
            _absorb(service, scope_id, scored, revisions)


async def acknowledge(service, scope_id, key, revision, score_scope):
    """Ignore stale ACKs; older clients cannot acknowledge above today's grade."""
    with _update_lock(service):
        if not _current(service):
            return
        current_revision = getattr(service.ranks, "calibration_revision", None)
        if revision is not None and revision != current_revision:
            return
        scored = score_scope(service, scope_id)
        if not _current(service, scored) or not scored["tier"]:
            return
        revisions = service.db.get_state(_REVISIONS, {})
        if revisions.get(scope_id) != current_revision:
            _absorb(service, scope_id, scored, revisions)
        # A deliberate slower PB can lower the grade within the same revision.
        # Its delayed ACK must not erase the next genuine climb either.
        accepted = min(key, scoring.progression_key(scored["tier"], scored["division"]))
        changed = service.acknowledge_watermark(scope_id, accepted)
    if changed:
        await service.publish_celebration_ack(scope_id, accepted)

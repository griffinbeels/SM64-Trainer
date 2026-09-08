"""Replay saved performances against one current calibration and original ROMs."""
from sm64_events.ranks import classify, curves, history
from sm64_events.ranks.calibration import resolve_curve
from sm64_events.tracking import marelo as marelo_bridge


def build_history(service, scope_id, groups, mode):
    resolved = {}

    def scorer(key, frames, context=None):
        identity = (key, (context or {}).get("game_version"))
        if identity not in resolved:
            resolved[identity] = resolve_curve(service.ranks, *identity)
        progress = curves.progress_for_time(resolved[identity], classify.display_cs(frames))
        return progress["score"] if progress else None

    feed = (marelo_bridge.pb_feed(service.db.pbs(), service.ranks.clock_for)
            if classify.RANK_MODES[mode]["order"] is None
            else marelo_bridge.successes_for(service.db.attempts(), service.ranks.clock_for))
    return {"scope_id": scope_id,
            "calibration_revision": service.ranks.calibration_revision,
            "basis": "Saved performances evaluated against current standards",
            "points": history.history_series(feed, groups, scorer, mode, context_scorer=scorer)}

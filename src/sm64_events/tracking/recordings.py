"""Public recording associations and conservative import metadata repair.

The journal retains the imported original; the database overlay retains edits,
including explicit removals. Reprojection rebuilds attempts without rewriting
that overlay. A source refresh fills a missing link only when both sides name
one imported performance. A played attempt is never a repair candidate.
"""
from collections import defaultdict

from sm64_events.core.timefmt import frame_at_or_after
from sm64_events.tracking.importing import IMPORT_EVENT


def _candidate_key(candidate):
    return (candidate.entity_key, candidate.strat_tag or None,
            candidate.timer_mode, candidate.game_version, candidate.platform,
            frame_at_or_after(candidate.time_cs))


def _event_key(payload):
    entity = (f"segment:{payload['segment_id']}"
              if payload.get("segment_id") is not None
              else f"star:{payload.get('course_id')}:{payload.get('star_id')}")
    return (entity, payload.get("strat_tag") or None,
            payload.get("timer_mode"), payload.get("game_version"),
            payload.get("platform"), payload.get("frames"))


def backfill_matches(events, source, candidates):
    """Yield `(attempt_id, url)` only for unambiguous source-row matches.

    Historical imports have no row key. They require a unique incoming row
    AND unique historical event with the same entire performance fingerprint.
    Distinct rows sharing a time, or duplicate historical events, are refused.
    Even known row keys require identical time/clock/ROM/platform: a source
    row updated with a new performance cannot donate it to an older attempt.
    """
    old, incoming = defaultdict(list), defaultdict(list)
    for event in events:
        if event.type == IMPORT_EVENT and event.payload.get("source") == source:
            old[_event_key(event.payload)].append(event)
    for candidate in candidates:
        incoming[_candidate_key(candidate)].append(candidate)
    for key, rows in incoming.items():
        prior = old.get(key, [])
        for candidate in rows:
            if not candidate.video or not candidate.row_key:
                continue
            same_row = [event for event in prior
                        if event.payload.get("row_key") == candidate.row_key]
            # Duplicate candidates for one row may disagree about the URL.
            identical = [row for row in rows if row.row_key == candidate.row_key]
            if len(identical) != 1:
                continue
            if len(same_row) == 1:
                yield same_row[0].id, candidate.video
            elif (not same_row and len(prior) == 1 and len(rows) == 1
                  and not prior[0].payload.get("row_key")):
                yield prior[0].id, candidate.video

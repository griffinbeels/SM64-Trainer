"""Historical winners, independent of surviving recordings and current PB slots.

Unknown legacy validity remains eligible unless the journal proves otherwise.
Availability is joined after winning identities are resolved: a missing winner
must never silently become a slower run whose recording survived.
"""
from sm64_events.ghost.sharing_catalog import _local_row
from sm64_events.ghost.sharing_targets import describe
from sm64_events.tracking.caveats import save_state_run


def best_key(row):
    return (row["kind"], row["entity_key"], row.get("strat"), row["clock"], row.get("game_version"))


def _eligible(attempt):
    return (attempt.outcome == "success" and not getattr(attempt, "cleared", False)
            and getattr(attempt, "valid", None) is not False and not save_state_run(attempt)
            and not (getattr(attempt, "segment_id", None) is None
                     and getattr(attempt, "timed_at", None) == "grab"))


def historical_bests(database, available=()):
    definitions = database.segment_defs()
    available = {row["attempt_id"]: row for row in available if row.get("attempt_id") is not None}
    winners = {}

    def offer(row):
        if type(row["frames"]) is not int or row["frames"] <= 0:
            return
        key = best_key(row)
        previous = winners.get(key)
        # Equal times retain their first stable identity, independent of disk.
        if previous is None or row["frames"] < previous["frames"]:
            winners[key] = row

    for attempt in database.attempts():
        if not _eligible(attempt):
            continue
        try:
            row = _local_row(attempt, definitions)
        except (ValueError, TypeError):
            continue
        found = available.get(attempt.id)
        offer({**row, "available": found is not None,
               "saved": bool(found and found.get("saved")),
               "reason": None if found else "This best's ghost recording is no longer available."})

    # Imported/manual times belong in the best-ever view even though they do
    # not have a local native recording. Never synthesize an attempt number.
    for pb in database.pbs() if hasattr(database, "pbs") else ():
        if pb.get("attempt_id") is not None:
            continue
        kind = "segment" if pb.get("segment_id") is not None else "star"
        key = str(pb["segment_id"]) if kind == "segment" else f"{pb['course_id']}-{pb['star_id']}"
        try:
            target = describe(kind, key, pb.get("strat_tag"), definitions)
        except (ValueError, TypeError):
            continue
        offer({"id": f"time:{pb['id']}", "source": "local", "attempt_id": None, "import_id": None,
               "author": "You", "kind": kind, "entity_key": key, "strat": pb.get("strat_tag"),
               "target": target, "target_name": target["name"], "frames": pb["frames"],
               "clock": pb["timer_mode"], "recorded_utc": pb["saved_utc"],
               "game_version": pb.get("game_version"), "practice": False, "outcome": "success",
               "available": False, "saved": False,
               "reason": "This time has no local ghost recording."})
    return list(winners.values())


def comparison(row, bests):
    """Only explicit version, same target/strategy/clock, real finishes compare."""
    if (row.get("game_version") is None or row.get("entity_key") is None
            or row.get("practice") or row.get("outcome") != "success"):
        return "unknown", None
    best = next((best for best in bests if best_key(best) == best_key(row)), None)
    if best is None:
        return "no_best", None
    return ("faster" if row["frames"] < best["frames"] else "not_faster"), best["frames"]

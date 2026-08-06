"""How much history does "don't attribute a reset nobody chose" destroy?

Griffin, 2026-08-05: "if I reset BUT HAVEN'T EXPLICITLY SELECTED ANYTHING...
then a reset should always be unassigned... Unless there is literally only 1
option, or unless the user has selected it."

The rule is cheap to state and expensive to get wrong: it deletes rows from
every journal it replays, retroactively, because attempts are derived on every
replay rather than stored. So this scores it the way the 100-coin and Bowser
suppressions were scored before shipping -- replay the REAL journal both ways
and diff, refusing to trust a rule that has only been reasoned about.

Run against every journal that matters:

    uv run python tools/measure_unchosen_resets.py data/tracker.db
    uv run python tools/measure_unchosen_resets.py \
        "$LOCALAPPDATA/SM64Trainer/tracker.db"

Reads through sqlite3's own online backup, so it is safe beside a live server.
"""
import dataclasses
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sm64_events.storage.db import Database                    # noqa: E402
from sm64_events.tracking import projection                    # noqa: E402
from sm64_events.tracking.projection import (replay,           # noqa: E402
                                              strat_overrides)
from sm64_events.tracking.segments import SegmentDef, arms_ambiently  # noqa: E402


def _online_backup(live_path: Path, backup_path: Path) -> None:
    src = sqlite3.connect(f"{live_path.resolve().as_uri()}?mode=ro", uri=True)
    dest = sqlite3.connect(str(backup_path))
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()


def _segment_defs_from(db: Database) -> list[SegmentDef]:
    from sm64_events.core.paths import bundled_defaults_seed
    from sm64_events.tracking.defaults import reconcile_defaults
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    reconcile_defaults(db, seed)
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    return [SegmentDef(**{k: row[k] for k in keys}) for row in db.segment_defs()]


def _old_rule(self, segment_id: int, outcome: str) -> bool:
    """The gate as it stood BEFORE this change: ambient defs only, and no
    'only one candidate' carve-out. Restored by monkeypatch so both rules run
    against byte-identical events in one process."""
    seg_def = self._segments.definition(segment_id)
    if seg_def is None or not arms_ambiently(seg_def.start_triggers):
        return False
    return outcome != "success" and self.target != ("segment", segment_id)


def main() -> None:
    live_path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/tracker.db")
    if not live_path.exists():
        print(f"ERROR: database not found: {live_path}", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix="measure_unchosen_") as scratch:
        backup_path = Path(scratch) / "snapshot.db"
        _online_backup(live_path, backup_path)
        db = Database(backup_path)
        try:
            events = db.events()
            defs = _segment_defs_from(db)
            saved_pbs = {row["attempt_id"] for row in db.pbs()} \
                if hasattr(db, "pbs") else set()
        finally:
            db.close()

    new_rule = projection.Projector._untargeted_failure_for_segment
    projection.Projector._untargeted_failure_for_segment = _old_rule
    try:
        before = {a.id: a for a in replay(events, segments=defs)[0]}
    finally:
        projection.Projector._untargeted_failure_for_segment = new_rule
    after = {a.id: a for a in replay(events, segments=defs)[0]}

    removed = [before[i] for i in before.keys() - after.keys()]
    added = [after[i] for i in after.keys() - before.keys()]
    changed = [i for i in before.keys() & after.keys()
               if before[i] != after[i]]

    print(f"=== {live_path} ===")
    print(f"{len(events)} events, {len(before)} attempts before, "
          f"{len(after)} after")
    print(f"removed: {len(removed)}   added: {len(added)}   "
          f"otherwise changed: {len(changed)}")

    # The three things that must be ZERO for this to be safe to ship.
    lost_success = [a for a in removed if a.outcome == "success"]
    # A HUMAN labelling a row is an explicit journal event. The attempt's own
    # `strat_tag` is NOT that signal -- every seeded castle movement carries
    # `default_strat = "Standard"`, so reading the tag counts the DEFAULT and
    # reports a safe rule as destroying labelled history. It did exactly that
    # on this tool's first run (7 of 8 "labelled"), which is why the metric is
    # the journal's own override events instead.
    hand_labelled = set(strat_overrides(events))
    lost_labelled = [a for a in removed if a.id in hand_labelled]
    lost_pb = [a for a in removed if a.id in saved_pbs]
    print(f"  successes removed:      {len(lost_success)}   (MUST be 0)")
    print(f"  labelled rows removed:  {len(lost_labelled)}   (MUST be 0)")
    print(f"  saved PBs removed:      {len(lost_pb)}   (MUST be 0)")

    by_segment = Counter(a.segment_id for a in removed)
    print("\nremoved rows by segment id (top 15):")
    for segment_id, count in by_segment.most_common(15):
        print(f"  segment {segment_id}: {count}")

    outcomes = Counter(a.outcome for a in removed)
    print(f"\nremoved rows by outcome: {dict(outcomes)}")


if __name__ == "__main__":
    main()

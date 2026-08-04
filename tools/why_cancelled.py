"""Why did that movement stop being ACTIVE? Replay the journal and say so.

`tools/measure_topology_cancels.py` answers the DESIGN question — "would these
rules have destroyed anything he really completed" — over a whole journal.
This answers the LIVE one: he watched a card go stale and wants the reason,
which is a different question and was previously answered by reading source
and guessing (2026-08-02: "Bowser 2 -> Upstairs breaks after going to the
lobby" — the lobby had nothing to do with it).

For every settled position change in the most recent session it prints the
move, whether the world graph calls it an edge, and for every definition
disarmed there: WHICH RULE fired, and the hop counts that decided it.

Two things it is careful about, both of which cost a wrong answer once:

- The verdict is stamped with the frame the MOVE happened on, not the frame it
  was judged on. `_flush_move` defers by one frame on purpose (the transient
  lobby), so a move made in a quiet stretch — inside a Bowser stage, where
  nothing journals for seconds — is not judged until the next position event.
  Read the judged frame and you blame whatever the player happened to do next.
- It reads the ENGINE's own state either side of the real `_flush_move`
  rather than re-deriving the rules, so this tool cannot drift from them.

Usage:
    uv run python tools/why_cancelled.py [db]         # default: data/tracker.db
"""
import dataclasses
import sqlite3
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sm64_events.storage.db import Database
from sm64_events.tracking import topology
from sm64_events.tracking.projection import replay
from sm64_events.tracking.segments import (SegmentDef, SegmentEngine,
                                           declared_nodes)


def _snapshot(live: Path) -> Path:
    """sqlite's OWN backup API, never a file copy -- the live db is WAL-mode
    and a server may be holding it open right now."""
    out = Path(tempfile.mkdtemp()) / "snapshot.db"
    src = sqlite3.connect(f"{live.resolve().as_uri()}?mode=ro", uri=True)
    dest = sqlite3.connect(str(out))
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()
    return out


def explain(db: Database, session_id: int | None = None) -> list[str]:
    keys = [field.name for field in dataclasses.fields(SegmentDef)]
    defs = [SegmentDef(**{key: row[key] for key in keys})
            for row in db.segment_defs()]
    by_id = {d.id: d for d in defs}
    events = db.events()
    if not events:
        return ["That journal has no events."]
    if session_id is None:
        session_id = max(e.session_id for e in events
                         if e.session_id is not None)
    events = [e for e in events if e.session_id in (None, session_id)]

    lines: list[str] = []
    original = SegmentEngine._flush_move

    def capturing(self, ev, notices):
        previous, pending = self._settled_node, self._pending_move
        armed_before = dict(self._armed)
        hops_before = {sid: self._next_step_hops(by_id[sid], arm, previous)
                       for sid, arm in armed_before.items() if sid in by_id}
        original(self, ev, notices)
        node = self._settled_node
        if previous is None or node is None or node == previous:
            return
        legal = topology.is_legal_move(previous, node)
        gone = sorted(set(armed_before) - set(self._armed))
        moved_on = pending[0] if pending else ev.frame
        lag = ev.frame - moved_on
        lines.append(
            f"\nf{moved_on}  {previous} -> {node}   edge={legal}"
            + (f"   (judged {lag} frames later)" if lag > 1 else ""))
        if not gone:
            lines.append("   nothing cancelled")
        for sid in gone:
            d = by_id.get(sid)
            if d is None:
                continue
            after = self._next_step_hops(d, armed_before[sid], node)
            rule = ("RULE 1 — not an edge, so the warp menu fabricated it"
                    if not legal else
                    f"RULE 2 — moved AWAY: {hops_before.get(sid)} hops to its "
                    f"next step, now {after}")
            lines.append(f"   CANCELLED {d.name!r} (id {sid})")
            lines.append(f"      {rule}")
            lines.append(f"      declared stops: {sorted(declared_nodes(d))}")

    SegmentEngine._flush_move = capturing
    try:
        replay(events, segments=defs)
    finally:
        SegmentEngine._flush_move = original
    return lines or ["No settled position changes in that session."]


def main() -> int:
    live = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/tracker.db")
    if not live.exists():
        print(f"No journal at {live}", file=sys.stderr)
        return 2
    print(f"Reading: {live}")
    for line in explain(Database(_snapshot(live))):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

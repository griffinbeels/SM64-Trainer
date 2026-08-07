"""Score a moment's time against Usamune's own screen — once, and for good.

WHY THIS EXISTS. `MomentDetector`'s offset from Usamune's raw counter has moved
by ±1 in three consecutive rounds (2026-08-05 twice, 2026-08-06), and every one
of those moves was argued from a comparison somebody RECALLED — "the practice
log reads about a frame fast" — rather than from two numbers standing in one
frame. Round 6's reasoning was sound and its premise was wrong, which is the
signature of an argument standing in for an instrument.

A STAR can score itself: Usamune writes its own answer into a result store and
`tools/derive_xcam.py` reads it back, so no human has to look at anything. A
DOOR writes nothing — it does not stop the clock — so the only ground truth
there has ever been is the number on his screen. This tool's whole job is to
make ONE screenshot enough to settle it.

THE ONE DESIGN DECISION WORTH KNOWING: every offset here is measured against
the RAW COUNTER the row carries (`payload.counter`), never against the time we
published. Our published number is the thing under suspicion and it changes
whenever the constant does — rows journaled before and after any flip would
otherwise be incomparable. Scored against the counter, a door from any round
scores identically, so evidence accumulates across rounds instead of resetting
with each one.

Usage:
    uv run python tools/score_moment_clock.py
        The last N moments with their raw counters, and what Usamune would
        read under each candidate offset. Play, open a door, screenshot the
        emulator, come back.

    uv run python tools/score_moment_clock.py --usamune 1'06\"83 0'53\"63
        Score one or more readings taken off his screen. Each reading names
        the row it belongs to and the offset it implies; the verdict says
        whether they agree with each other and with the shipped code.

    uv run python tools/score_moment_clock.py --db <path> --limit 20
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from sm64_events.core.snapshot import GameSnapshot  # noqa: E402
from sm64_events.core.timefmt import format_igt, parse_igt  # noqa: E402
from sm64_events.detectors.moment import MOMENTS, MomentDetector  # noqa: E402
from what_happened import (describe_age, label_for,  # noqa: E402
                           open_readonly, survey_journals)

# How far from the raw counter a reading may sit and still be believed to
# belong to that moment. Deliberately wider on both sides than any hypothesis
# on the table: a band that only admits the answer you expect cannot falsify
# it, and a NEGATIVE offset (we read the action a frame late) is one of the two
# live hypotheses in `MomentDetector`'s own docstring.
BAND = range(-3, 7)


@dataclass(frozen=True)
class Match:
    """One reading, attributed to one journal row, implying one offset."""
    row: dict
    offset: int


@dataclass(frozen=True)
class Scored:
    reading: str
    frames: int
    matches: tuple[Match, ...]

    @property
    def state(self) -> str:
        if not self.matches:
            return "unmatched"
        return "scored" if len(self.matches) == 1 else "ambiguous"


def score(rows: list[dict], readings: list[str], band=BAND) -> list[Scored]:
    """Attribute each screen reading to a journal row, and say by how much."""
    scored = []
    for reading in readings:
        frames = parse_igt(reading)
        matches = tuple(
            Match(row=row, offset=frames - row["counter"])
            for row in rows
            if (frames - row["counter"]) in band
        )
        scored.append(Scored(reading=reading, frames=frames, matches=matches))
    return scored


def verdict(scored: list[Scored]) -> tuple[int | None, str]:
    """The offset every scored reading agrees on, or why there isn't one."""
    settled = [s.matches[0].offset for s in scored if s.state == "scored"]
    if not settled:
        return None, "no reading could be attributed to a single moment"
    distinct = sorted(set(settled))
    if len(distinct) > 1:
        return None, (f"readings disagree: offsets {distinct} — Usamune's lead "
                      f"over the counter is not a constant, which is itself "
                      f"the finding")
    return distinct[0], f"{len(settled)} reading(s), unanimous"


def code_offset() -> int:
    """What the SHIPPED code adds to the counter, derived by running it.

    Not read off a constant and not restated here: a synthetic door edge goes
    through the real `MomentDetector` and the real `IgtClock`, so this number
    cannot drift from what the server would journal, whatever the constants
    are called or how many of them there are.
    """
    def snap(action: int, frame: int) -> GameSnapshot:
        return GameSnapshot(
            wall_time_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
            global_timer=frame, mario_action=action, mario_action_timer=0,
            num_stars=0, last_completed_course=0, last_completed_star=0,
            igt_overall=1000, curr_level=6, curr_area=1)

    door = next(iter(next(m for m in MOMENTS if m.kind == "door_open").actions))
    walking = 0x04000440
    detector = MomentDetector()
    detector.process(snap(walking, 100), snap(door, 101))
    # The SETTLE poll: a moment is a one-poll held emit since 2026-08-07 (its
    # landmark is re-read after the edge — detectors/moment.py), so the edge
    # alone publishes nothing. The number this scores is still the EDGE's.
    events = detector.process(snap(door, 101), snap(door, 102))
    return events[0].payload["igt_frames"] - 1000


def moment_rows(conn: sqlite3.Connection, limit: int) -> list[dict]:
    """The last `limit` moments, newest first, with what each one carries."""
    raw = conn.execute(
        "SELECT id, wall_time_utc, payload FROM events "
        "WHERE type = 'moment_reached' ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    rows = []
    for event_id, wall, payload_json in raw:
        payload = json.loads(payload_json)
        counter = payload.get("counter")
        if counter is None:
            # Journaled before the raw counter rode along. Scoring it would
            # mean deriving the counter back out of the number under
            # suspicion, so it is skipped rather than half-believed.
            continue
        landmark = payload.get("landmark") or {}
        rows.append({
            "id": event_id, "wall": wall, "kind": payload.get("kind"),
            "counter": counter, "ours": payload.get("igt_frames"),
            "action_timer": payload.get("action_timer"),
            "level": payload.get("level"), "area": payload.get("area"),
            "landmark": landmark.get("key"),
        })
    return rows


def render_table(rows: list[dict], offsets: range) -> str:
    header = (f"{'id':>6}  {'kind':<9} {'lvl:area':>8} {'counter':>8} "
              f"{'ours':>9} {'at':>3}  " +
              "  ".join(f"{'+' + str(o):>8}" for o in offsets))
    lines = [header, "-" * len(header)]
    for row in rows:
        candidates = "  ".join(
            f"{format_igt(row['counter'] + o):>8}" for o in offsets)
        ours = format_igt(row["ours"]) if row["ours"] is not None else "-"
        lines.append(
            f"{row['id']:>6}  {row['kind'] or '?':<9} "
            f"{str(row['level']) + ':' + str(row['area']):>8} "
            f"{row['counter']:>8} {ours:>9} {row['action_timer']!s:>3}  "
            f"{candidates}")
    return "\n".join(lines)


def render_scores(scored: list[Scored], shipped: int) -> str:
    lines = []
    for entry in scored:
        if entry.state == "unmatched":
            lines.append(
                f"  {entry.reading:>10} ({entry.frames} frames) — matches no "
                f"moment in the listing; widen --limit or check the journal")
            continue
        if entry.state == "ambiguous":
            ids = ", ".join(f"#{m.row['id']} (+{m.offset})"
                            for m in entry.matches)
            lines.append(f"  {entry.reading:>10} — AMBIGUOUS between {ids}")
            continue
        match = entry.matches[0]
        lines.append(
            f"  {entry.reading:>10} = counter {match.row['counter']} + "
            f"{match.offset}  (moment #{match.row['id']}, {match.row['kind']}, "
            f"we published {format_igt(match.row['ours'])})")
    offset, why = verdict(scored)
    lines.append("")
    if offset is None:
        lines.append(f"VERDICT: unsettled — {why}")
        return "\n".join(lines)
    lines.append(f"VERDICT: Usamune reads counter + {offset}  ({why})")
    if offset == shipped:
        lines.append(f"         the shipped code adds {shipped}. AGREES.")
    else:
        lines.append(
            f"         the shipped code adds {shipped}, so every moment it "
            f"journals is {abs(offset - shipped)} frame(s) "
            f"{'fast' if shipped < offset else 'slow'}.")
    return "\n".join(lines)


def pick_journal(explicit: str | None) -> tuple[Path, str] | None:
    """The freshest journal, through `what_happened.survey_journals` — ONE
    freshness policy on this machine, owned there (its docstring says why)."""
    if explicit:
        path = Path(explicit)
        return (path, "forced") if path.exists() else None
    now = datetime.now(timezone.utc)
    for path, count, stamp in survey_journals():
        if count and stamp is not None:
            return path, f"{label_for(path)}, newest {describe_age(stamp, now)}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--usamune", nargs="*", default=[], metavar="READING",
                        help="what the emulator showed, e.g. 1'06\"83")
    parser.add_argument("--limit", type=int, default=12,
                        help="how many recent moments to list (default 12)")
    parser.add_argument("--db", help="force one journal")
    args = parser.parse_args()

    picked = pick_journal(args.db)
    if picked is None:
        print("No journal with events found.")
        return 1
    journal, note = picked
    print(f"journal: {journal}  ({note})")

    conn = open_readonly(journal)
    rows = moment_rows(conn, args.limit)
    conn.close()
    if not rows:
        print("No moments carrying a raw counter. Play through a door first.")
        return 1

    shipped = code_offset()
    print(f"shipped code: Usamune counter + {shipped}\n")
    print(render_table(rows, range(0, 4)))
    if not args.usamune:
        print("\nNow read the emulator for one of these and re-run with"
              "\n  --usamune <what Usamune showed>")
        return 0
    print()
    print(render_scores(score(rows, args.usamune), shipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

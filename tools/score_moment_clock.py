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
from sm64_events.detectors.moment import Moment, MOMENTS, MomentDetector  # noqa: E402
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
    """The offset every scored reading agrees on, or why there isn't one.

    Operates on readings of ONE KIND — a caller comparing readings that
    matched rows of different kinds must partition with `scored_by_kind`
    first, because two kinds legitimately carrying different shipped offsets
    (a door's `counter + 2` beside a textbox's `counter + 3`, since round 3)
    is not disagreement, it is the registry working as designed.
    """
    settled = [s.matches[0].offset for s in scored if s.state == "scored"]
    if not settled:
        return None, "no reading could be attributed to a single moment"
    distinct = sorted(set(settled))
    if len(distinct) > 1:
        return None, (f"readings disagree: offsets {distinct} — Usamune's lead "
                      f"over the counter is not a constant, which is itself "
                      f"the finding")
    return distinct[0], f"{len(settled)} reading(s), unanimous"


def scored_by_kind(scored: list[Scored]) -> dict[str, list[Scored]]:
    """Partition SCORED (unambiguously matched) readings by the KIND of the
    row each one matched, so `verdict` and `code_offset` are asked about the
    same kind together — never a textbox reading checked against a door's
    constant. Unmatched/ambiguous readings carry no kind and stay out; they
    still get their own line in `render_scores`."""
    by_kind: dict[str, list[Scored]] = {}
    for entry in scored:
        if entry.state != "scored":
            continue
        by_kind.setdefault(entry.matches[0].row["kind"], []).append(entry)
    return by_kind


def _moment_for(kind: str) -> Moment:
    moment = next((m for m in MOMENTS if m.kind == kind), None)
    if moment is None:
        raise ValueError(f"no such moment kind: {kind!r}")
    return moment


def code_offset(kind: str = "door_open") -> int:
    """What the SHIPPED code adds to the counter for THIS KIND, derived by
    running it.

    Not read off a constant and not restated here: a synthetic edge for
    `kind` goes through the real `MomentDetector` — its `Moment.open_states`
    gate and `Moment.extra_lag_frames` included — and the real `IgtClock`, so
    this number cannot drift from what the server would journal, whatever the
    constants are called, how many of them there are, or which kind is asked
    about. A door and a textbox moved by DIFFERENT amounts on 2026-08-11
    (round 3: a textbox carries one frame beyond a door's own measured lag)
    precisely because they are two rows in one registry, not two copies of
    one number — so this reads the registry per kind rather than hardcoding
    either row's arithmetic. Defaults to `door_open` so a caller asking about
    "the shipped code" with no kind in mind gets the constant this tool has
    always reported.

    Raises for a kind with an EMPTY action set (`switch_press`,
    `enemy_defeated`, ...): those are CAUSED moments, published by
    `detectors/caused.py` off the object pool on its own edge, not by an
    action edge — a different code path this tool does not run.
    """
    moment = _moment_for(kind)
    if not moment.actions:
        raise ValueError(
            f"{kind!r} is a CAUSED moment (detectors/caused.py, empty action "
            f"set) — it has no action edge for this tool to run through "
            f"MomentDetector")
    # An action with a gate (`open_states`) may sit in `actions` without ever
    # opening one on its own — `ACT_WAITING_FOR_DIALOG` is in `DIALOG_ACTIONS`
    # but carries no entry in `BOX_OPENS_AT_STATE` (moment.py's own
    # docstring), so picking an arbitrary member of `actions` can land on one
    # that never fires. `open_states`' own keys are the ones that DO.
    action = next(iter(moment.open_states)) if moment.open_states \
        else next(iter(moment.actions))
    walking = 0x04000440

    def snap(mario_action: int, frame: int, state: int = 0) -> GameSnapshot:
        return GameSnapshot(
            wall_time_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
            global_timer=frame, mario_action=mario_action,
            mario_action_timer=0, mario_action_state=state,
            num_stars=0, last_completed_course=0, last_completed_star=0,
            igt_overall=1000, curr_level=6, curr_area=1)

    frame = 100
    snaps = [snap(walking, frame)]
    if moment.open_states is None:
        frame += 1
        snaps.append(snap(action, frame))
    else:
        # THE TURN, THEN THE BOX (moment.py's own docstring): entering the
        # action alone is not enough here — climb `mario_action_state` one
        # frame at a time, exactly as the poller would, until it reaches
        # this action's own open threshold.
        threshold = moment.open_states[action]
        for state in range(threshold + 1):
            frame += 1
            snaps.append(snap(action, frame, state))
    # The SETTLE poll: a moment is a one-poll held emit since 2026-08-07 (its
    # landmark is re-read after the edge — detectors/moment.py), so the edge
    # alone publishes nothing. The number this scores is still the EDGE's.
    snaps.append(snap(action, frame + 1, snaps[-1].mario_action_state))

    detector = MomentDetector()
    events = []
    for prev, curr in zip(snaps, snaps[1:]):
        events.extend(detector.process(prev, curr))
    return next(e for e in events
                if e.payload["kind"] == kind).payload["igt_frames"] - 1000


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


def render_scores(scored: list[Scored]) -> str:
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
    lines.append("")
    by_kind = scored_by_kind(scored)
    if not by_kind:
        lines.append("VERDICT: unsettled — no reading could be attributed to "
                     "a single moment")
        return "\n".join(lines)
    # ONE VERDICT PER KIND — never one shared shipped offset for every
    # reading, which is exactly the bug this replaced: a textbox scoring
    # `+3` against a door's `+2` read as a mismatch even when the textbox
    # was correct by design (round 3, 2026-08-11).
    for kind in sorted(by_kind):
        offset, why = verdict(by_kind[kind])
        if offset is None:
            lines.append(f"VERDICT ({kind}): unsettled — {why}")
            continue
        try:
            shipped = code_offset(kind)
        except ValueError as exc:
            lines.append(f"VERDICT ({kind}): +{offset} measured — {exc}")
            continue
        agrees = offset == shipped
        lines.append(
            f"VERDICT ({kind}): +{offset} measured, +{shipped} shipped — "
            f"{'AGREES' if agrees else 'MISMATCH'} ({why})")
        if not agrees:
            lines.append(
                f"         every {kind} the shipped code journals is "
                f"{abs(offset - shipped)} frame(s) "
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

    print("shipped code:")
    for kind in sorted({row["kind"] for row in rows if row["kind"]}):
        try:
            print(f"  {kind}: counter + {code_offset(kind)}")
        except ValueError as exc:
            print(f"  {kind}: {exc}")
    print()
    print(render_table(rows, range(0, 4)))
    if not args.usamune:
        print("\nNow read the emulator for one of these and re-run with"
              "\n  --usamune <what Usamune showed>")
        return 0
    print()
    print(render_scores(score(rows, args.usamune)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

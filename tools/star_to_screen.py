"""DEPRECATED 2026-08-04 — the output is NOISE. Do not trust a number from it.

Kept rather than deleted because the idea is right and two of its three faults
are already fixed; what is broken is the JOIN, and fixing that is a real change
rather than a tweak. His ruling: *"star_to_screen seems like a noisy
implementation and doesn't feel like useful instrumentation, so we probably
shouldn't use it for now… perhaps we could fix it one day."*

**THE JOIN IS NOT UNIQUE, and this file claimed it was.** `Broadcaster._seq`
starts at 0 on every server start, while `data/ui_log.jsonl` persists across
restarts — so seq 1234 from this session matches a paint recorded by a session
hours ago. That is what produced `broadcast` columns of -3,370,141 ms and
+5,124,888 ms in its first real run: not a clock problem, a WRONG-ROW problem.
The docstring below asserted "exact — no timestamp matching" and the tool
PRINTED that claim, unmeasured.

Fixing it needs a per-run identity on both sides — the journal's `session_id`
is already on every event row, and the marks would have to carry the one the
page is connected to. Until then every number here can be from another day.

The two faults already fixed, kept as the record of what to re-check:
* `readLogs` queried the attempt tables inside `.objective-card`; the log is
  its own `.attempts-card` section, so it returned an empty row list forever
  and the tool joined nothing at all;
* marks were claimed only when some surface changed, so an unclaimed set
  waited for a later unrelated paint and `render` absorbed the gap — a
  17-SECOND render for one grab.

Where does the time between the game and the SCREEN actually go?

    uv run python tools/star_to_screen.py

Live report 2026-08-04: *"It's still slow, and you did not succeed… Do we have
instrumentation for comparing the timegap between detecting the xcam / final
time, and when we actually display it in the frontend?"* We did not. Two rounds
of fixes were aimed by reading code, and they went to a real 1.5 s tail in the
DETECTOR that turned out not to be the thing he was feeling.

This joins the two logs that already existed and never met:

* `data/tracker.db` — the journal. Every broadcast event carries the `seq` it
  was journaled with, and `star_collected` additionally carries
  `published_after`, the frames the detector held it past the x-cam.
* `data/ui_log.jsonl` — what the page PAINTED, with `marks` naming the exact
  `seq` that caused the paint (`ui/latency.js`).

So the join is by identity, not by timestamp proximity, and the breakdown is
attributable rather than inferred:

    x-cam  --detector-->  journal  --ws-->  browser
                                             |
                                     --coalesce--> fetch --> paint

Every stage is a different culprit and a different fix. Read the column that is
big, not the one you expected.
"""
import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sm64_events.core.paths import data_root  # noqa: E402

FPS = 30.0


def _utc(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ms(start, end):
    if start is None or end is None:
        return None
    return (end - start).total_seconds() * 1000


def paints_by_seq(path: Path) -> dict:
    """The FIRST paint that named each seq. First, not last: a later render in
    the same burst is a repaint, and what he is waiting for is the moment the
    row became visible."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue            # a torn final line is skipped, never raised on
        marks = row.get("marks")
        if not isinstance(marks, dict) or marks.get("ws_seq") is None:
            continue
        seq = int(marks["ws_seq"])
        if seq in out:
            continue
        out[seq] = (row, marks)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=30,
                    help="how many recent grabs to show (default 30)")
    ap.add_argument("--surface", default="log",
                    help="which painted surface counts as 'shown' "
                         "(log = the practice log's own rows; 'any' = "
                         "whichever surface repainted, which is the same "
                         "commit and therefore the same paint time)")
    args = ap.parse_args()

    db_path = data_root() / "data" / "tracker.db"
    ui_path = data_root() / "data" / "ui_log.jsonl"
    paints = paints_by_seq(ui_path)
    if not paints:
        print(f"No paint marks in {ui_path}.")
        print("The page posts these itself, so an EMPTY log means the open tab "
              "is running JS from before this instrument shipped — reload it, "
              "then play. (Same trap the UI log's own docstring names.)")
        return 1

    print("*** DEPRECATED: this tool's join is not unique (broadcast seq "
          "restarts at 0 every server run while the UI log persists), so rows "
          "can "
          "pair a grab with a paint from another day. Read the module "
          "docstring before believing any number below. ***\n")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT seq, frame, wall_time_utc, payload FROM events "
        "WHERE type='star_collected' ORDER BY id DESC LIMIT ?",
        (args.limit * 4,)).fetchall()

    measured = _join(rows, paints, args.surface, args.limit)
    if not measured and args.surface != "any":
        # A surface that never matched is usually a READER fault, not a quiet
        # session — the first version of `readLogs` looked for the tables
        # inside the wrong card and returned an empty list forever. Fall back
        # rather than report nothing: any surface repainted by the same commit
        # carries the same paint time, so the breakdown is still true.
        measured = _join(rows, paints, "any", args.limit)
        if measured:
            print(f"NOTE: no '{args.surface}' paint named a grab — falling "
                  f"back to whichever surface repainted. If this persists, "
                  f"the '{args.surface}' reader is finding nothing.\n")

    if not measured:
        print(f"{len(paints)} paints carry marks, but none of them named a "
              f"star_collected seq.")
        print("Grab a star with the page open, then run this again.")
        return 1

    _report(measured)
    return 0


def _join(rows, paints, surface, limit):
    measured = []
    for seq, frame, wall, payload in rows:
        hit = paints.get(seq)
        if not hit:
            continue
        row, marks = hit
        if surface != "any" and row.get("surface") != surface:
            continue
        p = json.loads(payload)
        journal = _utc(wall)
        stages = {
            "detector": (p.get("published_after") or 0) / FPS * 1000,
            "broadcast": _ms(journal, _utc(marks.get("ws_utc"))),
            "coalesce": _ms(_utc(marks.get("ws_utc")),
                            _utc(marks.get("fetch_start_utc"))),
            "fetch": _ms(_utc(marks.get("fetch_start_utc")),
                         _utc(marks.get("fetch_done_utc"))),
            "render": _ms(_utc(marks.get("fetch_done_utc")),
                          _utc(row.get("client_utc"))),
        }
        total = sum(v for v in stages.values() if v is not None)
        measured.append((p.get("star_name") or "?", stages, total, wall))
        if len(measured) >= limit:
            break
    return measured


def _report(measured):
    head = f"{'star':<34}" + "".join(f"{k:>11}" for k in
                                     ("detector", "broadcast", "coalesce",
                                      "fetch", "render", "TOTAL"))
    print(head)
    print("-" * len(head))
    for star, stages, total, wall in measured:
        cells = "".join(
            f"{stages[k]:>10.0f}m" if stages[k] is not None else f"{'?':>11}"
            for k in ("detector", "broadcast", "coalesce", "fetch", "render"))
        print(f"{star[:33]:<34}{cells}{total:>10.0f}m")

    print()
    for key in ("detector", "broadcast", "coalesce", "fetch", "render"):
        vals = [s[key] for _, s, _, _ in measured if s[key] is not None]
        if not vals:
            continue
        vals.sort()
        p95 = vals[min(len(vals) - 1, int(len(vals) * 0.95))]
        print(f"  {key:<10} median {statistics.median(vals):>7.0f} ms   "
              f"p95 {p95:>7.0f} ms   max {max(vals):>7.0f} ms")
    totals = sorted(t for _, _, t, _ in measured)
    print(f"  {'TOTAL':<10} median {statistics.median(totals):>7.0f} ms   "
          f"p95 {totals[min(len(totals)-1, int(len(totals)*0.95))]:>7.0f} ms   "
          f"max {max(totals):>7.0f} ms")
    print(f"\n{len(measured)} grabs joined by seq (exact — no timestamp "
          f"matching). A stage reading '?' had no mark for it.")


if __name__ == "__main__":
    raise SystemExit(main())

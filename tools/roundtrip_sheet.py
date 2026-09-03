"""IMPORT a runner's Ultimate Sheet column, EXPORT it back, and diff every row.

His own loop, verbatim (2026-09-02): "we need to be able to IMPORT a column,
and be able to EXPORT that exact same column back into the sheet. If we can do
that, then we can be very confident that the tool works as expected... Import
their column given a clean practice history -> export the sheet column using
the scorecard tool -> do they match 100% on every single row? That's the goal."

**It runs entirely in memory.** He described the loop as a manual one -- copy
the tab into his test sheet, import, paste, compare -- and the manual version
is what got him banned from the live document for leaving a column in it. The
same question is answerable with no spreadsheet at all: fetch the sheet once,
import a runner through the REAL endpoint into a fresh database, export
through the REAL endpoint, and compare against that runner's own cells. No
paste, no ban, and it runs over ten runners in the time one manual pass takes.

A mismatch is CLASSIFIED rather than counted, because the classes mean
different things and only one of them is ever the export's fault:

  missing   the sheet has a time here, we exported nothing
  extra     we exported a time here, the sheet's cell is empty
  differs   both have a time and they are not the same

It also prints the import's OWN refusal reasons, because the `missing` class
is mostly them: a subsection nobody has linked to a segment, and a target the
mapping has never paired with an entity, can never round-trip however the
export behaves.

    uv run python tools/roundtrip_sheet.py                 # fullest + 10
    uv run python tools/roundtrip_sheet.py --only Raisn --verbose

Nothing here writes to any sheet, ever.
"""
import argparse
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from fastapi.testclient import TestClient                          # noqa: E402

from import_fixture import OfflineMemory, bundled_standards_seed   # noqa: E402
from sm64_events.library import source                             # noqa: E402
from sm64_events.library.export_column import (                    # noqa: E402
    _blocks, _find_item, sheet_time)
from sm64_events.library.sheet import read_rows                    # noqa: E402
from sm64_events.library.store import build_and_stamp              # noqa: E402
from sm64_events.ranks.standards import RankStandards              # noqa: E402
from sm64_events.server.app import create_app                      # noqa: E402
from sm64_events.server.broadcaster import Broadcaster             # noqa: E402
from sm64_events.server.poller import Poller                       # noqa: E402
from sm64_events.storage.db import Database                        # noqa: E402
from sm64_events.tracking.service import TrackerService            # noqa: E402


def walk_rows(rows, payload):
    """`(worksheet row number, SheetRow, target, item)` for every row from 2 to
    the sheet's last, in order -- through the export's OWN block walk.

    Reused deliberately rather than re-derived: this comparison is about the
    VALUES round-tripping, and a second row-to-item mapping here would turn
    every mapping question into a value question. The mapping has its own
    tests, round 19's block rule among them."""
    blocks = _blocks(payload.get("targets") or [])
    by_row = {row.row: row for row in rows}
    block_index = -1
    for number in range(2, max(by_row) + 1):
        row = by_row.get(number)
        if row is None:
            yield number, None, None, None
            continue
        if row.opens_target:
            block_index += 1
        block = blocks[block_index] if 0 <= block_index < len(blocks) else None
        target, item = _find_item(row, block)
        yield number, row, target, item


def expected_column(rows, payload, runner):
    """That runner's own column, one entry per worksheet row."""
    lines = []
    for _number, _row, _target, item in walk_rows(rows, payload):
        entry = next((e for e in ((item or {}).get("entries") or [])
                      if e.get("runner") == runner and e.get("time_cs")), None)
        lines.append(sheet_time(int(entry["time_cs"])) if entry else "")
    return lines


def row_labels(rows, payload):
    """What each row is CALLED and which KIND of thing it belongs to -- which
    is what decides whether the import could ever have landed it. A subsection
    needs a link, an entity-less target needs a mapping, a star approach should
    simply work."""
    labels = {}
    for number, row, target, _item in walk_rows(rows, payload):
        if row is None:
            continue
        key = (target or {}).get("entity_key") or "NO-ENTITY"
        kind = "subsection" if row.kind == "subsection" else key.split(":")[0]
        labels[number] = f"[{kind}] {row.label}"
    return labels


def roundtrip(runner, rows, payload, scratch):
    """Import `runner` into a fresh database through the real endpoint, export
    the column back, and answer `(expected, actual, import summary)`."""
    db_path = scratch / f"{abs(hash(runner))}.db"
    if db_path.exists():
        db_path.unlink()
    ranks = RankStandards(scratch / "rs.json", seed_path=bundled_standards_seed())
    ranks.load()
    broadcaster = Broadcaster()
    service = TrackerService(Database(db_path), broadcaster, ranks=ranks)
    app = create_app(Poller(OfflineMemory(), [], service), broadcaster,
                     service=service,
                     adoptions_path=scratch / "adoptions.json",
                     mode_path=scratch / "mode.json",
                     library_path=scratch / "library.json.gz")
    with TestClient(app) as client:
        landed = client.post("/api/import/sheet", json={"runner": runner})
        if landed.status_code != 200:
            raise SystemExit(f"import of {runner!r} failed: {landed.text[:300]}")
        summary = landed.json()
        actual = client.get("/api/scorecard/column").json()["lines"]
    return expected_column(rows, payload, runner), actual, summary


def kind_of(want, got):
    if want and not got:
        return "missing"
    return "extra" if got and not want else "differs"


def classify(expected, actual):
    """`(counts, [(row number, kind, sheet value, our value), ...])` over every
    disagreeing row."""
    pairs = zip(expected, actual, strict=False)
    rows = [(index + 2, kind_of(want, got), want, got)
            for index, (want, got) in enumerate(pairs) if want != got]
    counts = Counter(kind for _n, kind, _w, _g in rows)
    if len(expected) != len(actual):
        counts["length"] += abs(len(expected) - len(actual))
    return counts, rows


def fill_counts(payload):
    """`(rows each runner has filled, the order they first appear)` -- "Find
    the player who has the highest fill rate (row 805)". Counted from the
    payload rather than read out of that cell: the cell is a percentage of a
    denominator we would have to guess, and the count is what it stands for."""
    filled, order = Counter(), {}
    for target in payload["targets"]:
        for collection in ("approaches", "subsections"):
            for item in target.get(collection) or []:
                for entry in item.get("entries") or []:
                    name = entry.get("runner")
                    if name and entry.get("time_cs"):
                        filled[name] += 1
                        order.setdefault(name, len(order))
    return filled, order


def report(runner, expected, actual, summary, labels, verbose):
    """One runner's line, plus its diff when there is one; how many rows
    disagreed."""
    counts, rows = classify(expected, actual)
    total = sum(counts.values())
    verdict = "MATCH" if total == 0 else f"{total} rows differ"
    print(f"{runner:<22} sheet {sum(1 for line in expected if line):>4}  "
          f"exported {sum(1 for line in actual if line):>4}  "
          f"landed {summary.get('imported', '?'):>4}  -> {verdict}")
    if not total:
        return 0
    print(f"    {dict(counts)}")
    reasons = Counter(row.get("reason", "?")
                      for row in (summary.get("rejected") or []))
    if reasons:
        print(f"    import refused: {dict(reasons)}")
    for number, kind, want, got in (rows if verbose else rows[:6]):
        print(f"    row {number:<5} {kind:<8} sheet={want!r:<10} "
              f"ours={got!r:<10} {labels.get(number, '')[:52]}")
    return total


def pick_runners(payload, args):
    filled, order = fill_counts(payload)
    if args.only:
        return list(args.only), filled
    fullest = filled.most_common(1)[0][0]
    first = [name for name, _ in sorted(order.items(), key=lambda kv: kv[1])]
    return [fullest] + [n for n in first if n != fullest][:args.runners], filled


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runners", type=int, default=10,
                        help="how many runners besides the fullest column")
    parser.add_argument("--only", action="append", default=None,
                        help="test exactly this runner (repeatable)")
    parser.add_argument("--verbose", action="store_true",
                        help="print every mismatching row, not just six")
    args = parser.parse_args(argv)

    print("fetching the sheet…", flush=True)
    data = source.fetch()
    rows = read_rows(data)
    payload = build_and_stamp(data, {})
    print(f"sheet id {source.SHEET_ID} · revision "
          f"{payload.get('sheet_revision')} · {len(rows)} rows", flush=True)

    runners, filled = pick_runners(payload, args)
    print(f"testing {len(runners)} runners: "
          f"{', '.join(f'{n} ({filled[n]})' for n in runners)}\n", flush=True)

    labels = row_labels(rows, payload)
    scratch = Path(tempfile.mkdtemp(prefix="roundtrip-"))
    worst = 0
    for runner in runners:
        expected, actual, summary = roundtrip(runner, rows, payload, scratch)
        worst = max(worst, report(runner, expected, actual, summary, labels,
                                  args.verbose))
    print(f"\nworst runner: {worst} differing rows")
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

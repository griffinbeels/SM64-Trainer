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
  snapped   both have a time; the sheet's is one the timer cannot show
            and ours is the next frame up (50.92 -> 50.93). Reported,
            never a failure: a star here is a frame count, and rounding
            UP is the documented rule (`.claude/rules/import.md`). Six
            rows across eleven runners on 2026-09-04, every one of them
            a hand-typed centisecond off the 30-per-second set.

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
from sm64_events.library.audit import row_key                   # noqa: E402
from sm64_events.library.export_column import (                    # noqa: E402
    _blocks, _find_item, sheet_time)
from sm64_events.library.sheet import parse_time, read_rows        # noqa: E402
from sm64_events.library.store import build_and_stamp              # noqa: E402
from sm64_events.core.timefmt import frame_at_or_after             # noqa: E402
from sm64_events.ranks.classify import display_cs                  # noqa: E402
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
    """That runner's own column, one entry per worksheet row.

    The ROW's version selects the entry, not just the runner: build.py merges
    a (JP) and a (US) worksheet row into ONE payload item, so two rows share
    an entry list and the runner may hold a different time in each region.
    Taking the first match made the US row expect the JP time -- which read as
    the product disagreeing with the sheet when it was this function
    disagreeing with itself."""
    lines = []
    for _number, row, _target, item in walk_rows(rows, payload):
        mine = [e for e in ((item or {}).get("entries") or [])
                if e.get("runner") == runner and e.get("time_cs")]
        version = getattr(row, "version", None)
        if version and any(e.get("version") for e in mine):
            mine = [e for e in mine if e.get("version") == version]
        entry = mine[0] if mine else None
        lines.append(sheet_time(int(entry["time_cs"])) if entry else "")
    return lines


def expected_platforms(rows, payload, runner):
    """That runner's own PLATFORM per worksheet row, from the legend their
    column carries (round 29 item 2: `sheet.py::runner_legends`) -- None
    wherever the cell is empty or the column has no legend."""
    platforms = []
    for _number, row, _target, item in walk_rows(rows, payload):
        mine = [e for e in ((item or {}).get("entries") or [])
                if e.get("runner") == runner and e.get("time_cs")]
        version = getattr(row, "version", None)
        if version and any(e.get("version") for e in mine):
            mine = [e for e in mine if e.get("version") == version]
        platforms.append(mine[0].get("platform") if mine else None)
    return platforms


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
        tag = f"({row.version})" if row.version else ""
        labels[number] = f"[{kind}]{tag} {row.label}"
    return labels


def link_refused_rows(client, rows, payload, runner, limit):
    """Stand in for the segments he would build by hand, and LINK them.

    His question (2026-09-02): "if I *did* set up some of those missing
    subsections, and then we import, does it get detected correctly (imported
    correctly, exported correctly)? We can probably prove by induction that
    this would work for the rest."

    So this builds one segment per refused row -- a definition with no
    triggers, which is enough to exist and be adopted, since nothing here ever
    plays the game -- and points the row at it through the SAME
    `POST /api/library/adopt` door the Library tab's link control uses. What it
    cannot stand in for is whether the segment he builds actually MATCHES that
    stretch in game; it proves the plumbing from a linked row to a round-tripped
    cell, which is the half he asked about.

    Returns how many rows were linked."""
    wanted = []
    for _number, row, target, item in walk_rows(rows, payload):
        if row is None or item is None:
            continue
        if not any(entry.get("runner") == runner and entry.get("time_cs")
                   for entry in (item.get("entries") or [])):
            continue
        # Exactly the two classes the import refuses: a subsection, and an
        # approach whose target the mapping never paired with an entity.
        if row.kind == "subsection" or not (target.get("entity_key") or ""):
            wanted.append((target, item))
        if len(wanted) >= limit:
            break

    linked = 0
    for target, item in wanted:
        made = client.post("/api/segments", json={
            "name": f"{target.get('label')} — {item['name']}"[:120],
            # A definition needs at least one trigger to exist. Nothing here
            # ever plays the game, so a plain level entry/exit is enough to
            # be a real segment that a row can be adopted onto.
            "start_triggers": [{"type": "level_enter", "to": 6}],
            "end_triggers": [{"type": "level_exit", "from": 6}]})
        if made.status_code != 200:
            continue
        segment_id = made.json().get("id") or made.json().get("segment", {}).get("id")
        if segment_id is None:
            continue
        adopted = client.post("/api/library/adopt", json={
            "row_key": row_key(target, item["name"], item["ids"]),
            "entity_key": f"segment:{segment_id}"})
        linked += adopted.status_code == 200
    return linked


def roundtrip(runner, rows, payload, scratch, link=0):
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
        linked = link_refused_rows(client, rows, payload, runner, link) if link else 0
        landed = client.post("/api/import/sheet", json={"runner": runner})
        if landed.status_code != 200:
            raise SystemExit(f"import of {runner!r} failed: {landed.text[:300]}")
        summary = dict(landed.json(), linked=linked)
        exported = client.get("/api/scorecard/column")
        if exported.status_code != 200:
            raise SystemExit(f"export for {runner!r} failed: {exported.text[:300]}")
        body = exported.json()
        # Round 30: the column opens with the two legend cells (rows 2/3);
        # they are not the runner's values, so they are compared apart.
        legend = [(index + 2, cell["text"], cell["platform"])
                  for index, cell in enumerate(body["cells"]) if cell.get("legend")]
        actual = ["" if cell.get("legend") else cell["text"] for cell in body["cells"]]
        # The COLOUR round trip (round 29 item 2): a cell the runner's legend
        # stamped must come back with the same platform. Counted only over
        # rows whose value matched -- a value that did not round-trip has no
        # platform to compare -- and only for a runner with a legend.
        expected_platforms_ = expected_platforms(rows, payload, runner)
        actual_platforms = [cell["platform"] for cell in body["cells"]]
        stamped = [(number + 2, want, got) for number, (want, got, want_text, got_text)
                   in enumerate(zip(expected_platforms_, actual_platforms,
                                    expected_column(rows, payload, runner), actual,
                                    strict=False))
                   if want is not None and want_text == got_text]
        summary["legend"] = legend
        summary["platform_cells"] = len(stamped)
        summary["platform_mismatches"] = [(row, want, got) for row, want, got in stamped
                                          if want != got]
    return expected_column(rows, payload, runner), actual, summary


def kind_of(want, got):
    if want and not got:
        return "missing"
    if got and not want:
        return "extra"
    sheet_cs, our_cs = parse_time(want), parse_time(got)
    if (sheet_cs is not None and our_cs is not None
            and display_cs(frame_at_or_after(sheet_cs)) == our_cs):
        return "snapped"
    return "differs"


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


def by_kind(expected, actual, labels):
    """Per row KIND: how many of the runner's filled cells came back exactly,
    and how each of the rest failed.

    The kinds fail for different reasons and only one of them is about
    strategies, so a single total hides the answer to "do all the stars get
    added correctly with each strategy correctly used". The kind is the one
    `row_labels` stamps -- a star approach, a subsection, a segment, or a
    target the mapping never paired."""
    buckets = {}
    for index, (want, got) in enumerate(zip(expected, actual, strict=False)):
        label = labels.get(index + 2)
        if label is None or not (want or got):
            continue
        kind = label.split("]")[0].lstrip("[")
        bucket = buckets.setdefault(kind, Counter())
        bucket["cells"] += bool(want)
        bucket["exact" if want == got else kind_of(want, got)] += 1
    return buckets


def report(runner, expected, actual, summary, labels, verbose):
    """One runner's line, plus its diff when there is one; how many rows
    disagreed."""
    counts, rows = classify(expected, actual)
    # A snapped cell is the sheet holding a number the timer cannot show;
    # it is reported beside the verdict and never counts against it.
    total = sum(count for kind, count in counts.items() if kind != "snapped")
    snapped = counts.get("snapped", 0)
    verdict = ("MATCH" if total == 0 else f"{total} rows differ") + (
        f" ({snapped} snapped)" if snapped else "")
    print(f"{runner:<22} sheet {sum(1 for line in expected if line):>4}  "
          f"exported {sum(1 for line in actual if line):>4}  "
          f"landed {summary.get('imported', '?'):>4}  "
          f"linked {summary.get('linked', 0):>3}  -> {verdict}")
    held = Counter(row.get("reason", "?") for row in (summary.get("held") or []))
    if summary.get("legend"):
        print(f"    legend rows: {summary['legend']}")
    stamped = summary.get("platform_cells", 0)
    if stamped:
        # The colour round trip, for a runner whose column carries a legend.
        wrong = summary.get("platform_mismatches") or []
        print(f"    platform: {stamped - len(wrong)} of {stamped} stamped cells "
              f"came back on the same machine"
              + (f"; wrong: {wrong[:6]}" if wrong else ""))
    if not total:
        if held:
            print(f"    import held: {dict(held)}")
        return len(summary.get("platform_mismatches") or [])
    print(f"    {dict(counts)}")
    for kind, bucket in sorted(by_kind(expected, actual, labels).items(),
                               key=lambda kv: -kv[1]["cells"]):
        rest = {k: v for k, v in bucket.items() if k not in ("cells", "exact")}
        share = bucket["exact"] / bucket["cells"] if bucket["cells"] else 0
        print(f"      {kind:<12} {bucket['exact']:>4} of {bucket['cells']:>4} "
              f"exact ({share:5.1%})   {rest or ''}")
    reasons = Counter(row.get("reason", "?")
                      for row in (summary.get("rejected") or []))
    if reasons:
        print(f"    import refused: {dict(reasons)}")
    held = Counter(row.get("reason", "?") for row in (summary.get("held") or []))
    if held:
        # Round 28: a row the import cannot place is HELD rather than dropped,
        # and prints back from the hold -- so it round-trips, and the count
        # here says how much of a MATCH is held rather than landed.
        print(f"    import held: {dict(held)}")
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
    parser.add_argument("--link", type=int, default=0, metavar="N",
                        help="before importing, build a segment for the first N "
                             "rows the import would refuse and link them -- the "
                             "induction test for the subsections he would set up "
                             "by hand")
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
        expected, actual, summary = roundtrip(runner, rows, payload,
                                              scratch, link=args.link)
        worst = max(worst, report(runner, expected, actual, summary, labels,
                                  args.verbose))
    print(f"\nworst runner: {worst} differing rows")
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

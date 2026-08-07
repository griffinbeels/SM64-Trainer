"""Rebuild the bundled Ultimate Sheet library snapshot.

Sibling of tools/scrape_ranks.py. Transport verified 2026-08-04: the workbook
exports over plain HTTP with no auth, and the .xlsx form is the only one
carrying the video links -- CSV drops every one of them.

READ THE `unknown:` LIST this prints. It is the deliverable, not the noise: a
target we cannot name is a hole in the library, and it looks identical to a
target the sheet does not have.

Re-run: `uv run python tools/scrape_sheet.py`
        `uv run python tools/scrape_sheet.py --from <file.xlsx>`  (offline)"""
import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sm64_events.library.audit import load_overrides           # noqa: E402
from sm64_events.library.adopt import adoptable, summary        # noqa: E402
from sm64_events.library.build import build, coverage          # noqa: E402
from sm64_events.library.ladders import fit_payload            # noqa: E402
from sm64_events.library.mapping import UNMAPPED_EXPECTED      # noqa: E402
from sm64_events.library.source import export_url, fetch       # noqa: E402

OVERRIDES = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
             / "data" / "library_overrides.json")

# Gzipped, and measured rather than assumed: 4.51 MB of JSON compresses to
# 0.42 MB and costs 7 ms more to load (26 ms against 19 ms). The exe ships
# this file as-is, so that is a tenfold saving there for nothing a user could
# feel. Git stores both at about the same size, and a regenerated 4.5 MB JSON
# has no readable line diff either way.
OUT = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
       / "data" / "sheet_library.seed.json.gz")
# Its OWN file, never merged into rank_standards.seed.json: that is what makes
# "a fitted ladder cannot overwrite a vetted one" structural instead of a rule
# somebody has to remember.
LADDERS_OUT = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
               / "data" / "sheet_ladders.seed.json")
VETTED = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
          / "data" / "rank_standards.seed.json")


def utc_now() -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def _redundant(data: bytes, overrides: dict) -> int:
    """How many saved corrections the parser would now make by itself."""
    from sm64_events.library.audit import row_key
    plain = build(data, fetched_at="")
    kinds = {}
    for target in plain["targets"]:
        for item in target["approaches"]:
            kinds[row_key(target, item["name"], item["ids"])] = "approach"
        for item in target["subsections"]:
            kinds[row_key(target, item["name"], item["ids"])] = "subsection"
    return sum(1 for key, verdict in overrides["rows"].items()
               if verdict.get("kind") and kinds.get(key) == verdict["kind"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path,
                        help="read a saved .xlsx instead of fetching")
    args = parser.parse_args()

    data = args.source.read_bytes() if args.source else fetch()
    overrides = load_overrides(OVERRIDES)
    payload = build(data, fetched_at=utc_now(), overrides=overrides)
    # Fitted AFTER the corrections, because a row the audit moved between
    # approaches and subsections keeps its own times either way, but a row the
    # audit re-pointed at another entity must be fitted as what it now is.
    fit_payload(payload)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so re-running with no sheet change produces byte-identical output
    # and an empty git diff, rather than a 0.4 MB blob whose only change is a
    # timestamp.
    with gzip.GzipFile(OUT, "wb", compresslevel=9, mtime=0) as out:
        out.write(json.dumps(payload, indent=1, ensure_ascii=False).encode("utf-8"))

    vetted_seed = json.loads(VETTED.read_text(encoding="utf-8"))
    vetted = {ek: {s: l for s, l in e.get("strategies", {}).items() if l}
              for ek, e in vetted_seed["entities"].items()}
    # Entities whose strategy names are variant-qualified; a bare name cannot
    # identify a ladder there, so nothing is adopted onto them.
    qualified = {ek for ek, e in vetted_seed["entities"].items()
                 if e.get("exit_variants")}
    adopted = adoptable(payload, vetted, qualified)
    LADDERS_OUT.write_text(json.dumps(
        {"version": 2, "source": "sheet",
         "sheet_revision": payload["sheet_revision"],
         "model": payload["ladder_model"]["percentiles"],
         "entities": adopted}, indent=1, ensure_ascii=False, sort_keys=True),
        encoding="utf-8", newline="")

    cov = coverage(payload)
    size_mb = OUT.stat().st_size / 1_000_000
    print(f"wrote {OUT} ({size_mb:.1f} MB)")
    applied = len(overrides["targets"]) + len(overrides["rows"])
    if applied:
        print(f"  applied {applied} audit corrections from "
              f"{OVERRIDES.name} ({len(overrides['targets'])} targets, "
              f"{len(overrides['rows'])} rows)")
        redundant = _redundant(data, overrides)
        if redundant:
            # A correction the parser now makes on its own says the CLASS was
            # fixed rather than the instance patched. Reported, never deleted:
            # it is a human's ruling, and it is the thing that goes red if the
            # rule ever regresses.
            print(f"  {redundant} of them the parser now produces unaided "
                  f"-- the rule behind them is in the code, not just the file")
    print(f"  sheet revision {payload['sheet_revision']}")
    print(f"  {cov['targets']} targets, {cov['mapped']} mapped onto "
          f"{cov['entities']} entities, {cov['unmapped']} unmapped")
    model = payload["ladder_model"]
    print(f"  fitted ladders on {model['fitted_rows']} rows "
          f"({model['rows_too_thin']} too thin, under {model['min_entries']} times)")
    print(f"  {cov['approaches']} approaches, {cov['subsections']} subsections, "
          f"{cov['entries']} entries, {cov['videos']} videos, "
          f"{cov['runners']} runners")
    adopt_stats = summary(adopted, payload)
    print(f"  wrote {LADDERS_OUT.name}: {adopt_stats['strategies']} strategies "
          f"across {adopt_stats['entities']} entities that no vetted ladder "
          f"already describes ({adopt_stats['jp_ladders']} carry their own JP ladder)")
    drift = payload.get("styling_drift") or []
    if drift:
        print(f"  {len(drift)} rows where the sheet's BOLD disagrees with the "
              f"structural boundary (reported, not fatal):")
        for row in drift[:10]:
            print(f"    row {row['row']}: {row['label'][:52]}")
    for reason, count in sorted(cov["by_reason"].items()):
        flag = "" if reason in UNMAPPED_EXPECTED else "   <-- REVIEW"
        print(f"  unmapped/{reason}: {count}{flag}")
    for target in payload["targets"]:
        if target["entity_key"] is None and target["miss_reason"] == "unknown":
            print(f"    unknown: {target['section']} | {target['label']}")


if __name__ == "__main__":
    main()

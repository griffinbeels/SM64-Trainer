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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sm64_events.library.build import build, coverage          # noqa: E402
from sm64_events.library.mapping import UNMAPPED_EXPECTED      # noqa: E402

SHEET_ID = "1J20aivGnvLlAuyRIMMclIFUmrkHXUzgcDmYa31gdtCI"
# Gzipped, and measured rather than assumed: 4.51 MB of JSON compresses to
# 0.42 MB and costs 7 ms more to load (26 ms against 19 ms). The exe ships
# this file as-is, so that is a tenfold saving there for nothing a user could
# feel. Git stores both at about the same size, and a regenerated 4.5 MB JSON
# has no readable line diff either way.
OUT = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
       / "data" / "sheet_library.seed.json.gz")


def export_url(fmt: str = "xlsx") -> str:
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format={fmt}"


def fetch() -> bytes:
    with urllib.request.urlopen(export_url(), timeout=180) as response:
        return response.read()


def utc_now() -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", type=Path,
                        help="read a saved .xlsx instead of fetching")
    args = parser.parse_args()

    data = args.source.read_bytes() if args.source else fetch()
    payload = build(data, fetched_at=utc_now())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so re-running with no sheet change produces byte-identical output
    # and an empty git diff, rather than a 0.4 MB blob whose only change is a
    # timestamp.
    with gzip.GzipFile(OUT, "wb", compresslevel=9, mtime=0) as out:
        out.write(json.dumps(payload, indent=1, ensure_ascii=False).encode("utf-8"))

    cov = coverage(payload)
    size_mb = OUT.stat().st_size / 1_000_000
    print(f"wrote {OUT} ({size_mb:.1f} MB)")
    print(f"  sheet revision {payload['sheet_revision']}")
    print(f"  {cov['targets']} targets, {cov['mapped']} mapped onto "
          f"{cov['entities']} entities, {cov['unmapped']} unmapped")
    print(f"  {cov['approaches']} approaches, {cov['subsections']} subsections, "
          f"{cov['entries']} entries, {cov['videos']} videos, "
          f"{cov['runners']} runners")
    for reason, count in sorted(cov["by_reason"].items()):
        flag = "" if reason in UNMAPPED_EXPECTED else "   <-- REVIEW"
        print(f"  unmapped/{reason}: {count}{flag}")
    for target in payload["targets"]:
        if target["entity_key"] is None and target["miss_reason"] == "unknown":
            print(f"    unknown: {target['section']} | {target['label']}")


if __name__ == "__main__":
    main()

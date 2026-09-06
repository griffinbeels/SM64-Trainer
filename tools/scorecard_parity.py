"""IMPORT runners one at a time, point the Scorecard at all of them, and diff every tile.

His definition of done, verbatim (round 34, 2026-09-05): "if I import a
player, and then set the scorecard to track that player, our definition of
done here is that for any given player we do that for, the scorecard should
match exactly... We need to find a combination of test players that results
in 100% coverage across every single row of the spreadsheet. If we were to
import all of their times, and then set the scorecard to all of them, it
should be +0.00 across every row. At each step (importing player 1 -> setting
scorecard to player 1, additionally importing player 2 -> setting scorecard
to player1 + player2, etc) it should be +0.00 for all cards, by definition."

Runs entirely in memory against the library snapshot on disk (the user's own
refreshed copy when there is one, else the bundled seed) -- the import door
lands from `library.payload` without a fetch, so no network and no
spreadsheet is touched. Each step imports one more runner through the REAL
endpoint into a fresh database, sets the REAL goal (a runner goal, then a
multi goal of every runner so far, both regions on) and reads the REAL card.

A tile is reported when YOU and GOAL are both present and differ, or when
GOAL has a time and YOU has none (the import left a sheet time out). A tile
where YOU has a time and GOAL none is reported too, apart: an imported time
the goal cannot see. Every reported tile is EXPLAINED from both sides -- the
runner's sheet entries that map to the entity (target, row, slot, time,
region) and the database's PB rows and held times for it -- so the cause is
read off the report rather than guessed.

    uv run python tools/scorecard_parity.py                      # greedy cover of the card
    uv run python tools/scorecard_parity.py --runner RONC3NA --verbose
    uv run python tools/scorecard_parity.py --cover 10 --print-cover

Nothing here writes to any sheet, ever.
"""
import argparse
import gzip
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from fastapi.testclient import TestClient                          # noqa: E402

from import_fixture import OfflineMemory, bundled_standards_seed   # noqa: E402
from sm64_events.core.paths import bundled_sheet_library, sheet_library_path  # noqa: E402
from sm64_events.library.adoptions import sheet_strategy           # noqa: E402
from sm64_events.library.audit import row_key                      # noqa: E402
from sm64_events.library.ratings import runner_times               # noqa: E402
from sm64_events.ranks.classify import display_cs                  # noqa: E402
from sm64_events.ranks.standards import RankStandards              # noqa: E402
from sm64_events.server.app import create_app                      # noqa: E402
from sm64_events.server.broadcaster import Broadcaster             # noqa: E402
from sm64_events.server.poller import Poller                       # noqa: E402
from sm64_events.storage.db import Database                        # noqa: E402
from sm64_events.tracking.service import TrackerService            # noqa: E402
from sm64_events.tracking.views import latest_pbs_by_strategy      # noqa: E402

REGIONS = ["us", "jp"]          # the default walk: both on, the faster wins


def default_library() -> Path:
    own = sheet_library_path()
    if own.exists():
        return own
    bundled = bundled_sheet_library()
    if bundled is None:
        raise SystemExit("no library snapshot on disk")
    return bundled


def load_payload(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def open_app(scratch: Path, library_path: Path):
    """The real app on a fresh database, reading a COPY of `library_path`.

    The copy is not caution, it is required: `LibraryStore` owns the file it
    is given and rewrites it (a load re-derives, an import absorbs), so
    handing it the bundled seed or the user's own snapshot edits that file.
    Measured 2026-09-05, and it does not fail loudly -- pointing the store at
    the bundled seed stripped every `matched_strategy` from it and turned 14
    unrelated tests red in the next full run, with a tracked file quietly
    modified in the worktree."""
    scratch.mkdir(parents=True, exist_ok=True)
    library_copy = scratch / "library.json.gz"
    library_copy.write_bytes(Path(library_path).read_bytes())
    db_path = scratch / "parity.db"
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
                     library_path=library_copy)
    return app, service


def goal_for(runners: list[str]) -> dict:
    if len(runners) == 1:
        return {"kind": "runner", "runner": runners[0]}
    return {"kind": "multi",
            "sources": [{"kind": "runner", "runner": name} for name in runners]}


def card_tiles(client, scope: str) -> list[dict]:
    card = client.get(f"/api/scorecard?scope={scope}")
    if card.status_code != 200:
        raise SystemExit(f"scorecard failed: {card.text[:300]}")
    tiles = []
    for row in card.json()["rows"]:
        for tile in row["tiles"]:
            tiles.append(dict(tile, row=row["label"]))
    return tiles


def classify(tile: dict) -> str | None:
    you, goal = tile.get("you_cs"), tile.get("goal_cs")
    if you is None and goal is None:
        return None
    if goal is None:
        return "goal_missing"
    if you is None:
        return "you_missing"
    return "differs" if you != goal else None


def _time(cs: int | None) -> str:
    if cs is None:
        return "—"
    minutes, rest = divmod(int(cs), 6000)
    seconds, cents = divmod(rest, 100)
    return f"{minutes}'{seconds:02d}\"{cents:02d}"


def sheet_side(payload: dict, runners: set[str], key: str) -> list[str]:
    """Every sheet entry by one of `runners` whose row maps to `key`."""
    lines = []
    for target in payload.get("targets") or []:
        if target.get("entity_key") != key:
            continue
        for kind, collection in (("approach", "approaches"),
                                 ("subsection", "subsections")):
            for item in target.get(collection) or []:
                for entry in item.get("entries") or []:
                    if entry.get("runner") not in runners:
                        continue
                    region = entry.get("version") or target.get("version") or "-"
                    lines.append(
                        f"sheet  {_time(entry['time_cs'])}  {entry['runner']:<12} "
                        f"[{region}] {kind} {target.get('label')!r} / {item.get('name')!r}"
                        f" -> slot {sheet_strategy(target, item, kind)!r}"
                        f"{'  (vetted ' + item['matched_strategy'] + ')' if item.get('matched_strategy') else ''}")
    return lines


def db_side(payload: dict, service, key: str) -> list[str]:
    parts = key.split(":")
    lines = []
    for (course, star, segment, mode, strat, _rom), row in sorted(
            latest_pbs_by_strategy(service.db.pbs()).items(), key=lambda kv: str(kv[0])):
        if parts[0] == "star" and (course, star) != (int(parts[1]), int(parts[2])):
            continue
        if parts[0] == "segment" and segment != int(parts[1]):
            continue
        lines.append(f"pb     {_time(display_cs(row['frames']))}  strat {strat!r} "
                     f"[{row.get('game_version') or '-'}] {mode}")
    rows_by_key = {}
    for target in payload.get("targets") or []:
        if target.get("entity_key") != key:
            continue
        for collection in ("approaches", "subsections"):
            for item in target.get(collection) or []:
                rows_by_key[row_key(target, item.get("name") or "", item.get("ids") or ())] = (
                    target.get("label"), item.get("name"))
    for held in service.db.held_times():
        where = rows_by_key.get(held["row_key"])
        if where:
            lines.append(f"held   {_time(held['time_cs'])}  {where[0]!r} / {where[1]!r} "
                         f"[{held.get('game_version') or '-'}]")
    return lines


def importable_rows(payload: dict, keys: set[str]) -> dict[str, set[str]]:
    """{runner: the worksheet rows they have a time on that CAN land on the
    card} -- an approach row of a star target the card carries. His coverage
    is "every single row of the spreadsheet": a subsection or a segment row
    is held by the import and never reaches a tile, so it is not a row this
    check can cover; the rows here are the ones a mismatch could hide in."""
    reach: dict[str, set[str]] = {}
    for target in payload.get("targets") or []:
        if target.get("entity_key") not in keys:
            continue
        for item in target.get("approaches") or []:
            key = row_key(target, item.get("name") or "", item.get("ids") or ())
            for entry in item.get("entries") or []:
                if entry.get("runner"):
                    reach.setdefault(entry["runner"], set()).add(key)
    return reach


def greedy_cover(payload: dict, keys: set[str], limit: int
                 ) -> tuple[list[str], int, int, set[str]]:
    """Runners, most-new-rows first, until every importable worksheet row is
    covered or `limit` runners are picked. Answers (runners, rows covered,
    rows total, card tiles still uncovered)."""
    reach = importable_rows(payload, keys)
    universe = set().union(*reach.values()) if reach else set()
    picked, uncovered = [], set(universe)
    while uncovered and len(picked) < limit:
        best = max(reach, key=lambda name: (len(reach[name] & uncovered), -len(name), name))
        gain = reach[best] & uncovered
        if not gain:
            break
        picked.append(best)
        uncovered -= gain
    tiles_reached: set[str] = set()
    for region in REGIONS:
        for runner, times in runner_times(payload, {}, version=region).items():
            if runner in picked:
                tiles_reached.update(k for k in times if k in keys)
    return picked, len(universe) - len(uncovered), len(universe), keys - tiles_reached


def run(runners: list[str], payload: dict, library_path: Path, scope: str,
        verbose: bool, regions: list[str] = REGIONS) -> int:
    scratch = Path(tempfile.mkdtemp(prefix="parity-"))
    app, service = open_app(scratch, library_path)
    worst = 0
    with TestClient(app) as client:
        assert client.put("/api/scorecard/regions",
                          json={"regions": regions}).status_code == 200
        for step, runner in enumerate(runners, 1):
            landed = client.post("/api/import/sheet", json={"runner": runner})
            if landed.status_code != 200:
                raise SystemExit(f"import of {runner!r} failed: {landed.text[:300]}")
            body = landed.json()
            so_far = runners[:step]
            assert client.put("/api/scorecard/goal",
                              json=goal_for(so_far)).status_code == 200
            tiles = card_tiles(client, scope)
            graded = [tile for tile in tiles if tile.get("goal_cs") is not None]
            bad = [(classify(tile), tile) for tile in tiles if classify(tile)]
            worst = max(worst, len(bad))
            print(f"step {step}: +{runner} (imported {body.get('imported')}, "
                  f"held {len(body.get('held') or [])}) -> goal covers "
                  f"{len(graded)}/{len(tiles)} tiles, {len(bad)} mismatching", flush=True)
            shown = bad if verbose else bad[:6]
            for kind, tile in shown:
                print(f"  {kind:<12} {tile['row']} · {tile['label']}  YOU {_time(tile.get('you_cs'))}"
                      f"  GOAL {_time(tile.get('goal_cs'))}  [{tile['key']}]")
                for line in sheet_side(payload, set(so_far), tile["key"]):
                    print("      " + line)
                for line in db_side(payload, service, tile["key"]):
                    print("      " + line)
            if len(bad) > len(shown):
                print(f"  … {len(bad) - len(shown)} more (--verbose)")
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", type=Path, default=None,
                        help="library snapshot to import from (default: the user's own, else bundled)")
    parser.add_argument("--runner", action="append", default=None,
                        help="import exactly these runners, in this order (repeatable)")
    parser.add_argument("--cover", type=int, default=10,
                        help="without --runner: pick up to N runners that together cover the card")
    parser.add_argument("--scope", default="overall")
    parser.add_argument("--print-cover", action="store_true",
                        help="print the picked runners as --runner flags and stop")
    parser.add_argument("--regions", default=",".join(REGIONS),
                        help="which ROM regions the card includes, e.g. us or us,jp")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    regions = [region for region in args.regions.split(",") if region]

    library_path = args.library or default_library()
    payload = load_payload(library_path)
    print(f"library {library_path} · revision {payload.get('sheet_revision')} · "
          f"{len(payload.get('targets') or [])} targets", flush=True)

    runners = args.runner
    if not runners:
        scratch = Path(tempfile.mkdtemp(prefix="parity-cover-"))
        app, _ = open_app(scratch, library_path)
        with TestClient(app) as client:
            keys = {tile["key"] for tile in card_tiles(client, args.scope)}
        runners, covered, total, uncovered = greedy_cover(payload, keys, args.cover)
        print(f"cover: {len(runners)} runners hold {covered}/{total} importable worksheet rows "
              f"and reach {len(keys) - len(uncovered)}/{len(keys)} tiles"
              + (f"; tiles unreached: {sorted(uncovered)}" if uncovered else ""), flush=True)
        if args.print_cover:
            print(" ".join(f"--runner {name}" for name in runners))
            return 0
    worst = run(runners, payload, library_path, args.scope, args.verbose, regions)
    print(f"\nworst step: {worst} mismatching tiles")
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

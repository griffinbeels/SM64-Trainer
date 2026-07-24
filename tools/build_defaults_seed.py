"""Generate src/sm64_events/data/defaults.seed.json from the corpus tables.

The JSON is the artifact the app reads (tracking/defaults.reconcile_defaults
loads it at startup); this tool is how it is AUTHORED. Compact Python tables in
corpus_*.py expand here into the verbose seed shape, so 55 movement segments
cannot drift from one another and a route step stays one readable line.

Mirrors tools/scrape_ranks.py -> data/rank_standards.seed.json: generated
input, checked-in artifact, reconcile at startup.

    uv run python tools/build_defaults_seed.py            # write
    uv run python tools/build_defaults_seed.py --check    # diff only, exit 1

tests/test_build_defaults_seed.py runs --check's comparison, so the checked-in
JSON can never silently drift from these tables. NEVER hand-edit the JSON.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpus_legacy        # noqa: E402
import corpus_movements     # noqa: E402
import corpus_routes_main   # noqa: E402
import corpus_routes_stage  # noqa: E402
from corpus_vocab import CASTLE_MOVEMENT, ROUTE_SCOPED  # noqa: E402

SEED_VERSION = 2
OUT = (Path(__file__).resolve().parent.parent
       / "src" / "sm64_events" / "data" / "defaults.seed.json")


def _movement_row(row: dict) -> dict:
    """Expand a compact movement into a seed segment. Every movement is
    route-scoped and Castle Movement by construction — that uniformity is the
    whole reason this table is generated rather than hand-written."""
    return {"seed_key": row["seed_key"], "name": row["name"], "enabled": True,
            "start_triggers": [row["start"]],
            "end_triggers": [row["end"]],
            "waypoints": [[clause] for clause in row["via"]],
            "guards": ROUTE_SCOPED, "category": CASTLE_MOVEMENT}


def build() -> dict:
    """The whole seed, segments before routes (reconcile resolves route
    candidates' seed_key -> local segment_id in that order)."""
    segments = list(corpus_legacy.SEGMENTS)
    segments += [_movement_row(row) for row in corpus_movements.MOVEMENTS]
    routes = list(corpus_routes_main.ROUTES) + list(corpus_routes_stage.ROUTES)
    return {"seed_version": SEED_VERSION, "segments": segments,
            "routes": routes}


def render(seed: dict) -> str:
    return json.dumps(seed, indent=2, ensure_ascii=False) + "\n"


def main(argv) -> int:
    seed = build()
    text = render(seed)
    if "--check" in argv:
        on_disk = OUT.read_bytes().decode("utf-8") if OUT.exists() else ""
        if on_disk != text:
            print(f"OUT OF DATE: {OUT}\n"
                  "  run: uv run python tools/build_defaults_seed.py")
            return 1
        print(f"up to date: {OUT}")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT}: {len(seed['segments'])} segments, "
          f"{len(seed['routes'])} routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

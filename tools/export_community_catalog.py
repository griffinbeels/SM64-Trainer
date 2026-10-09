"""Build a catalog from bundled reference data, using only a scratch profile."""
import argparse
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from sm64_events.core.paths import bundled_defaults_seed, bundled_rank_standards, bundled_sheet_library
from sm64_events.ghost.community_catalog import export_catalog
from sm64_events.library.store import LibraryStore
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.app import _create_library_adoptions
from sm64_events.storage.db import Database
from sm64_events.tracking.defaults import reconcile_defaults


def build_catalog():
    with tempfile.TemporaryDirectory(prefix="sm64-community-catalog-") as scratch:
        root = Path(scratch)
        ranks = RankStandards(root / "ranks.json", seed_path=bundled_rank_standards())
        ranks.load()
        database = Database(root / "catalog.db")
        try:
            reconcile_defaults(database, json.loads(Path(bundled_defaults_seed()).read_text(encoding="utf-8")))
            library = LibraryStore(root / "sheet.json.gz", bundled_sheet_library())
            library.load()
            service = SimpleNamespace(db=database, ranks=ranks)
            _create_library_adoptions(library, service, root / "adoptions.json")
            return export_catalog(ranks, database.segment_defs())
        finally:
            database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = build_catalog()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Exported {len(catalog['entries'])} canonical target/ROM entries; generation {catalog['generation']}.")


if __name__ == "__main__":
    main()

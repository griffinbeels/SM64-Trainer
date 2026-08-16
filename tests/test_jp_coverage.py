"""Every annotated JP difference resolves through the standards store
(tools/check_jp_coverage.py -- task 0065's "if there's a JP/US distinction, it
needs to be updated in the tool", as a gate). Mutation-proved 2026-08-15:
losing the JP overlay (jp_deltas -> {}) reports 789 mismatches."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import check_jp_coverage  # noqa: E402


def test_every_annotated_jp_ladder_resolves():
    report = check_jp_coverage.check()
    assert report["mismatches"] == [], report["mismatches"][:10]
    # Floors, not equalities -- both seeds grow. The vetted floor is the
    # 2026-08-07 measurement; the sheet floor is the fitted layer as adopted.
    assert report["vetted"]["strategies"] >= 70, report["vetted"]
    assert report["sheet"]["strategies"] >= 5, report["sheet"]


def test_the_gate_has_teeth(tmp_path):
    """The check must go red when the JP overlay is lost, or a green run
    proves nothing."""
    from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders
    from sm64_events.ranks.standards import RankStandards
    store = RankStandards(tmp_path / "rs.json", bundled_rank_standards(),
                          bundled_sheet_ladders())
    store.load()
    store.jp_deltas = lambda ek, strat: {}
    assert check_jp_coverage.check(store)["mismatches"]

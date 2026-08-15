"""Does every annotated JP difference reach the standards store as a JP ladder?

Task 0065's second half, as a gate rather than a claim: "every single
strategy for every single star / segment is updated with this in mind. If
there's a JP/US distinction, it needs to be updated in the tool." Two
annotation sources exist and this walks both against the REAL store:

  vetted   the Daily Star seed's sparse `jp_strategies` (tools/scrape_ranks.py
           emits one only where the published JP time differs from US)
  sheet    the Ultimate Sheet layer's fitted JP ladders (a row whose JP and US
           populations both carried enough runs to fit)

For each (entity, strategy) with a JP annotation, the store's
`ladders(ek, "jp")` must differ from `ladders(ek, "us")` for that strategy,
and every annotated rank must resolve to its annotated JP value. A JP
annotation that resolves to the US ladder is a JP difference the tool has
lost -- exactly the silent failure the task warns about.

Run: `uv run python tools/check_jp_coverage.py` -- prints the counts and every
mismatch, exit 1 on any. tests/test_jp_coverage.py runs the same function.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders  # noqa: E402
from sm64_events.ranks.standards import RankStandards  # noqa: E402


def _annotations(seed_path: Path) -> dict:
    """{(ek, strat): {rank: jp_seconds}} for one seed file's jp_strategies."""
    data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    out = {}
    for ek, entity in data.get("entities", {}).items():
        for strat, ladder in (entity.get("jp_strategies") or {}).items():
            if ladder and strat in (entity.get("strategies") or {}):
                out[(ek, strat)] = ladder
    return out


def check(store: RankStandards | None = None) -> dict:
    """{'vetted': {...counts}, 'sheet': {...counts}, 'mismatches': [...]}"""
    if store is None:
        scratch = Path(tempfile.mkdtemp(prefix="jp-coverage-")) / "rank_standards.json"
        store = RankStandards(scratch, bundled_rank_standards(), bundled_sheet_ladders())
        store.load()
    sources = {"vetted": _annotations(bundled_rank_standards()),
               "sheet": _annotations(bundled_sheet_ladders())}
    report = {"mismatches": []}
    for name, annotations in sources.items():
        entities = {ek for ek, _ in annotations}
        report[name] = {"strategies": len(annotations), "entities": len(entities)}
        for (ek, strat), annotated in annotations.items():
            us = store.ladders(ek, "us").get(strat, {})
            jp = store.ladders(ek, "jp").get(strat, {})
            if not jp:
                report["mismatches"].append(
                    f"{name}: {ek} / {strat}: no JP ladder resolves at all")
                continue
            if jp == us:
                report["mismatches"].append(
                    f"{name}: {ek} / {strat}: JP resolves identical to US")
            for rank, seconds in annotated.items():
                # The user's file (vetted) sits ON TOP of the sheet layer, so a
                # sheet annotation may legitimately lose to a vetted one for
                # the same rank; only report a rank that resolves to neither.
                if jp.get(rank) != seconds and (
                        name == "vetted" or jp.get(rank) is None):
                    report["mismatches"].append(
                        f"{name}: {ek} / {strat} / {rank}: annotated {seconds} "
                        f"resolves to {jp.get(rank)}")
            if strat not in store.jp_strategies(ek):
                report["mismatches"].append(
                    f"{name}: {ek} / {strat}: missing from jp_strategies()")
    return report


def main() -> int:
    report = check()
    for name in ("vetted", "sheet"):
        counts = report[name]
        print(f"{name}: {counts['strategies']} strategies with a JP difference "
              f"across {counts['entities']} entities")
    print(f"mismatches: {len(report['mismatches'])}")
    for line in report["mismatches"]:
        print("  " + line)
    return 1 if report["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())

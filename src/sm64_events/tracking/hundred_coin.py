"""Which 100-coin strategy a finished run belongs to, given the star it ended on.

A main course's 100-coin run is timed separately per EXIT star — the star you
grab to leave the course once the 100th coin has given you the star (spec
2026-08-03-hundred-coin-exit-variants). The exit star is therefore a
DISCRIMINATOR on the strategy: xcams publishes CCM's run twice, once ended on
Slip Slidin' Away and once on the Big Penguin Race, and both of those define a
strategy called "Standard".

The split this module encodes is between what the system OBSERVES and what the
user CHOOSES. The exit star is observed — the closing `star_collected` says
which star it was, and no preference can make that a different star. The
sub-strategy inside a variant ("Standard" vs "Open") is the user's, and nothing
here may overrule it. So a run that ends on a variant other than the selected
one moves to that variant and KEEPS the sub-strategy: `100c + Slide · Open`
plus a Big Penguin Race exit is `100c + Race · Open`, not
`100c + Race · Standard` (user's ruling, 2026-08-03).

Pure and import-free of the standards STORE on purpose — it takes the two maps
it needs — so it can be driven straight from a test and injected into the
projector without dragging a file-backed store into replay.
"""

from sm64_events.ranks.standards import VARIANT_SEP


def _variant_of(exit_variants: dict, strategy: str):
    """(label, exit star) for `strategy`, longest label wins; None if unfiled.
    Deliberately the same rule as `RankStandards.variant_of` and deliberately
    not a call into it — this module never sees the store."""
    best = None
    for label, star in exit_variants.items():
        if strategy and strategy.startswith(label + VARIANT_SEP) and (
                best is None or len(label) > len(best[0])):
            best = (label, star)
    return best


def classify(strategies, exit_variants: dict, current: str | None,
             exit_star: int | None) -> str | None:
    """The strategy a 100-coin run ending on `exit_star` should be filed under.

    `strategies` is the entity's ladder names in seed order; `exit_variants` is
    its {label: exit star} map. Returns None for "no answer — leave whatever is
    remembered alone", which covers a failed run (no exit star at all) and an
    exit star the community has no times for. That None is what makes a failed
    100-coin run stay unlabelled, and so stay prunable, exactly as it was
    before this feature existed.
    """
    if exit_star is None or not exit_variants:
        return None
    candidates = [s for s in strategies
                  if (found := _variant_of(exit_variants, s))
                  and found[1] == exit_star]
    if not candidates:
        return None
    if current in candidates:
        return current                      # already right — never churn
    held = _variant_of(exit_variants, current or "")
    if held is not None:
        leaf = current[len(held[0]) + len(VARIANT_SEP):]
        for candidate in candidates:
            found = _variant_of(exit_variants, candidate)
            if candidate[len(found[0]) + len(VARIANT_SEP):] == leaf:
                return candidate            # same sub-strategy, new variant
    return candidates[0]

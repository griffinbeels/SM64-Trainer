"""Pure rank classification. THE canonical rank ORDER lives here (the store
adds colors). Times are compared in DISPLAYED centiseconds so the rank never
disagrees with the time the user sees (project rule: Usamune IGT clock)."""

# hardest -> easiest. Iron is the implicit floor: it carries NO threshold in
# data; a completion slower than the easiest defined tier ranks Iron.
#
# DUPLICATED IN JS on purpose (`ui/components/caps.js::CAP`, which is where the
# tier's name/colour/treatment live) and held together by
# tests/test_cross_language_parity.py, not by this comment. Adding a tier here
# alone renders it as its own raw key in fallback grey on a ladder one rung
# short, and throws nothing.
RANK_NAMES = ["Mario", "Grandmaster", "Master", "Diamond", "Platinum",
              "Gold", "Silver", "Bronze", "Iron"]
RANK_SCORE = {n: len(RANK_NAMES) - i for i, n in enumerate(RANK_NAMES)}

def display_cs(frames: int) -> int:
    """Total centiseconds AS format_igt displays them (30 fps quantized)."""
    return (frames // 30) * 100 + (frames % 30) * 100 // 30


def _present(ladder_cs: dict) -> list[str]:
    return [r for r in RANK_NAMES if r in ladder_cs and r != "Iron"]


def rank_for(ladder_cs: dict, time_cs: int) -> str | None:
    """Best tier (hardest) whose upper-bound the time beats; Iron if slower
    than every defined tier; None if the ladder is empty."""
    present = _present(ladder_cs)
    if not present:
        return None
    for r in present:                       # hardest first
        if time_cs <= ladder_cs[r]:
            return r
    return "Iron"


def next_tier(ladder_cs: dict, rank: str | None) -> str | None:
    """The next HARDER defined tier above `rank` (None at the top)."""
    if rank is None:
        return None
    present = _present(ladder_cs)
    if rank == "Iron":
        return present[-1] if present else None
    if rank not in present:
        return None
    i = present.index(rank)
    return present[i - 1] if i > 0 else None


def resolve_cutoff_videos(ladder_cs: dict, clips, overrides=None) -> dict:
    """{rank: url} for a strategy: per tier, the fastest example whose OWN time
    RANKS that tier (band model), with manual `overrides` winning per rank (and
    able to add a tier no clip reaches). Reuses rank_for so a video's tier never
    disagrees with the displayed rank; Iron (the floor — no cutoff row) is never
    auto-assigned. `clips` is [[record_cs, url], ...]."""
    best = {}                                # rank -> (cs, url)
    for cs, url in clips or []:
        rank = rank_for(ladder_cs, cs)
        if not rank or rank == "Iron":
            continue
        if rank not in best or cs < best[rank][0]:
            best[rank] = (cs, url)
    out = {rank: cu[1] for rank, cu in best.items()}
    for rank, url in (overrides or {}).items():
        if url:
            out[rank] = url
    return out


# Rank-mode registry (average rank mode spec): HOW an entity-level rank
# display picks the time it grades. order None = the saved per-strategy PB
# row (no averaging); "recent" = the last `window` valid runs; "top" = the
# `window` fastest ever; window None = every valid run. Adding a mode is one
# row here AND one in ui/components/ranks.js RANK_MODE_OPTIONS (ids, labels and
# ORDER; the picker renders that list directly and PUT /api/ranks/mode 409s on
# an id this dict lacks). tests/test_cross_language_parity.py fails if the two
# stop agreeing — a mode added on one side only ships invisible or ships broken.
RANK_MODES = {
    "pb":       {"label": "PB",       "window": None, "order": None},
    "avg10":    {"label": "Avg 10",   "window": 10,   "order": "recent"},
    "avg50":    {"label": "Avg 50",   "window": 50,   "order": "recent"},
    "best10":   {"label": "Best 10",  "window": 10,   "order": "top"},
    "best50":   {"label": "Best 50",  "window": 50,   "order": "top"},
    "lifetime": {"label": "Lifetime", "window": None, "order": "recent"},
}
DEFAULT_RANK_MODE = "pb"


def average_frames(frames_list: list[int], window: int | None,
                   order: str) -> tuple[int, int] | None:
    """(mean_frames, count_used) over the selected slice of `frames_list`
    (chronological), or None when empty. order "recent" keeps the last
    `window` entries, "top" the fastest `window`; window None takes all.
    Fewer than `window` entries -> mean of what exists (count tells)."""
    if not frames_list:
        return None
    if order == "top":
        chosen = sorted(frames_list)[:window] if window else sorted(frames_list)
    else:
        chosen = frames_list[-window:] if window else list(frames_list)
    return round(sum(chosen) / len(chosen)), len(chosen)

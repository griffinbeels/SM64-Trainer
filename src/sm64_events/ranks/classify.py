"""Pure rank classification. THE canonical rank ORDER lives here (the store
adds colors). Times are compared in DISPLAYED centiseconds so the rank never
disagrees with the time the user sees (project rule: Usamune IGT clock)."""

# hardest -> easiest. Iron is the implicit floor: it carries NO threshold in
# data; a completion slower than the easiest defined tier ranks Iron.
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


def band(ladder_cs: dict, time_cs: int) -> dict:
    """Banner data: current rank, next tier, remaining gap (cs), bar fill
    (0..1). fill/next are None at the top tier; fill is 0 at the Iron floor."""
    rank = rank_for(ladder_cs, time_cs)
    if rank is None:
        return {"rank": None, "next": None, "gap_cs": None, "fill": None}
    nxt = next_tier(ladder_cs, rank)
    if nxt is None:                          # top tier -> no bar
        return {"rank": rank, "next": None, "gap_cs": None, "fill": None}
    gap = time_cs - ladder_cs[nxt]
    if rank == "Iron":                       # floor -> no band start
        return {"rank": rank, "next": nxt, "gap_cs": gap, "fill": 0.0}
    span = ladder_cs[rank] - ladder_cs[nxt]
    fill = (ladder_cs[rank] - time_cs) / span if span > 0 else 1.0
    return {"rank": rank, "next": nxt, "gap_cs": gap,
            "fill": max(0.0, min(1.0, fill))}

"""Reusable scraper for sm64-xcams rank standards -> the bundled seed JSON.

Transport (verified 2026-06-23): the site is a Next.js SPA that embeds its
precomputed rank standards in a static chunk as a `JSON.parse('{...}')`
literal. We fetch the page, locate the chunk, extract that blob, and map its
xcams entity keys to the trainer's (course/star)/segment identity.

Entity keys are "<stageIdx>_<starKey>": stages 0-14 are the main courses,
15 is Castle Secret Stars, 16 is Bowser Courses. Times are centiseconds.
Re-run: `uv run python tools/scrape_ranks.py`."""
import json

_RANKS = ["Mario", "Grandmaster", "Master", "Diamond", "Platinum",
          "Gold", "Silver", "Bronze", "Iron"]


def parse_standards(raw: dict) -> dict:
    """xcams standards object -> {xcams_key: {strategy: {rank: seconds}}}.
    Excludes ranks whose cell is not sr=='time' (the Iron floor; sometimes Bronze)."""
    out = {}
    for key, strats in raw.items():
        ladders = {}
        for strat, body in strats.items():
            times = body.get("times", {})
            ladder = {}
            for rank in _RANKS:
                cell = times.get(rank)
                if not cell or cell.get("sr") != "time":
                    continue
                cs = cell.get("time", {}).get("time")
                if isinstance(cs, (int, float)):
                    ladder[rank] = round(cs / 100, 2)
            if ladder:
                ladders[strat] = ladder
        if ladders:
            out[key] = ladders
    return out

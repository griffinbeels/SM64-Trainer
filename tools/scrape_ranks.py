"""Reusable scraper for sm64-xcams rank standards -> the bundled seed JSON.

Transport (verified 2026-06-23): the site is a Next.js SPA that embeds its
precomputed rank standards in a static chunk as a `JSON.parse('{...}')`
literal. We fetch the page, locate the chunk, extract that blob, and map its
xcams entity keys to the trainer's (course/star)/segment identity.

Entity keys are "<stageIdx>_<starKey>": stages 0-14 are the main courses,
15 is Castle Secret Stars, 16 is Bowser Courses. Times are centiseconds.
Re-run: `uv run python tools/scrape_ranks.py`."""
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sm64_events.ranks.standards import entity_key  # noqa: E402

# Closed vocabulary — update if xcams adds tiers (order = fastest to slowest).
_RANKS = ["Mario", "Grandmaster", "Master", "Diamond", "Platinum",
          "Gold", "Silver", "Bronze", "Iron"]

_SECRET = {"wc": 21, "vc": 22, "mc": 20, "aqua": 24, "wmotr": 23, "pss": 19}
_BOWSER = {"1n": 5, "2n": 6, "3n": 7, "1x": 8, "2x": 9, "3x": 10}  # No Reds=pipe, Battle=Bowser

# Movement segments with no xcams source -> hand-authored RTA defaults (seconds).
DEFAULT_SEGMENT_LADDERS = {
    1: {"Standard": {"Mario": 8.0, "Gold": 9.0, "Silver": 10.0}},   # LBLJ
    2: {"Standard": {"Mario": 6.0, "Gold": 7.0, "Silver": 8.0}},    # MIPS Clip
    3: {"Standard": {"Mario": 5.0, "Gold": 6.0, "Silver": 7.0}},    # Lakitu Skip
    4: {"Standard": {"Mario": 4.0, "Gold": 5.0, "Silver": 6.0}},    # BitS Entry
}


def parse_standards(raw: dict) -> dict[str, dict[str, dict[str, float]]]:
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


def key_to_entity(key: str) -> str | None:
    stage, _, star = key.partition("_")
    if not stage.isdigit():
        return None
    s = int(stage)
    if 0 <= s <= 14:
        return entity_key(s + 1, int(star) - 1) if star.isdigit() else None
    if s == 15:
        c = _SECRET.get(star)
        return entity_key(c, 0) if c else None
    if s == 16:
        seg = _BOWSER.get(star)
        return entity_key(None, None, seg) if seg else None
    return None


def build_seed(parsed: dict) -> dict:
    entities = {}
    for key, ladders in parsed.items():
        ek = key_to_entity(key)
        if ek is None:
            continue
        clock = "rta" if ek.startswith("segment:") else "igt"
        entities[ek] = {"clock": clock, "strategies": ladders}
    for seg_id, strategies in DEFAULT_SEGMENT_LADDERS.items():
        entities.setdefault(f"segment:{seg_id}", {"clock": "rta", "strategies": strategies})
    return {"version": 1, "entities": entities}


def extract_standards_blob(js_text: str) -> dict:
    """Find the JSON.parse('...') literal holding the rank standards (a dict whose
    values are {strat: {'times': {...}}}) and return the parsed object."""
    needle = "JSON.parse('"
    i = 0
    while True:
        j = js_text.find(needle, i)
        if j < 0:
            raise LookupError("standards blob not found in chunk")
        k = j + len(needle)
        buf = []
        while k < len(js_text):
            c = js_text[k]
            if c == "\\":
                buf.append(js_text[k:k + 2]); k += 2; continue
            if c == "'":
                break
            buf.append(c); k += 1
        i = k + 1
        try:
            obj = json.loads("".join(buf).encode().decode("unicode_escape"))
        except ValueError:
            continue
        if isinstance(obj, dict) and obj:
            v = next(iter(obj.values()))
            if isinstance(v, dict) and v:
                strat = next(iter(v.values()))
                t = strat.get("times") if isinstance(strat, dict) else None
                if isinstance(t, dict) and any(r in t for r in _RANKS):
                    return obj


def fetch_standards() -> dict:
    """Fetch the live site, locate the chunk holding the standards, parse it."""
    base = "https://sm64-xcams.netlify.app"
    page = urllib.request.urlopen(base + "/beta", timeout=30).read().decode("utf-8", "replace")
    chunks = sorted(set(re.findall(r"/_next/static/chunks/[\w./-]+\.js", page)))
    for path in chunks:
        js = urllib.request.urlopen(base + path, timeout=30).read().decode("utf-8", "replace")
        if "Grandmaster" in js:
            try:
                return extract_standards_blob(js)
            except LookupError:
                continue
    raise LookupError("could not locate standards chunk")


def main() -> None:
    seed = build_seed(parse_standards(fetch_standards()))
    out = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
           / "data" / "rank_standards.seed.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seed, indent=1))
    print(f"wrote {out} ({len(seed['entities'])} entities)")


if __name__ == "__main__":
    main()

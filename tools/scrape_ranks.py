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


def _resolve_jp_us(cell) -> tuple | None:
    """(jp_cs, us_cs) for a rank cell, or None if not a timed cell.
    cell['time'] = {'time': primary, 'alt': [other, 'us'|'jp'] | None}.
    Resolve by alt LABEL: 'us' alt -> primary is JP; 'jp' alt -> primary is US."""
    if not cell or cell.get("sr") != "time":
        return None
    t = cell.get("time", {})
    prim = t.get("time")
    if not isinstance(prim, (int, float)):
        return None
    alt = t.get("alt")
    if not alt:
        return prim, prim
    other, label = alt[0], alt[1]
    if label == "us":
        return prim, other
    if label == "jp":
        return other, prim
    return prim, prim


def parse_standards(raw: dict) -> dict[str, dict[str, dict[str, float]]]:
    """xcams standards -> {key: {strat: {rank: US-effective seconds}}}.
    US where a US time exists, else JP. Excludes non-timed (Iron floor) cells."""
    out = {}
    for key, strats in raw.items():
        ladders = {}
        for strat, body in strats.items():
            times = body.get("times", {})
            ladder = {}
            for rank in _RANKS:
                ju = _resolve_jp_us(times.get(rank))
                if ju is None:
                    continue
                ladder[rank] = round(ju[1] / 100, 2)   # us-effective
            if ladder:
                ladders[strat] = ladder
        if ladders:
            out[key] = ladders
    return out


def parse_jp_deltas(raw: dict) -> dict:
    """{key: {strat: {rank: JP seconds}}} for ranks whose JP time differs from
    US. Sparse: omits strats/entities with no differences. For future JP support."""
    out = {}
    for key, strats in raw.items():
        ent = {}
        for strat, body in strats.items():
            times = body.get("times", {})
            deltas = {}
            for rank in _RANKS:
                ju = _resolve_jp_us(times.get(rank))
                if ju is None:
                    continue
                jp, us = ju
                if jp != us:
                    deltas[rank] = round(jp / 100, 2)
            if deltas:
                ent[strat] = deltas
        if ent:
            out[key] = ent
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


def _all_blobs(js_text: str) -> list:
    """Every JSON.parse('...') literal in the chunk, parsed (skips unparseable)."""
    out, i, needle = [], 0, "JSON.parse('"
    while True:
        j = js_text.find(needle, i)
        if j < 0:
            return out
        k = j + len(needle); buf = []
        while k < len(js_text):
            c = js_text[k]
            if c == "\\":
                buf.append(js_text[k:k + 2]); k += 2; continue
            if c == "'":
                break
            buf.append(c); k += 1
        i = k + 1
        try:
            out.append(json.loads("".join(buf).encode().decode("unicode_escape")))
        except ValueError:
            pass


def _is_standards(obj) -> bool:
    if not (isinstance(obj, dict) and obj):
        return False
    v = next(iter(obj.values()))
    if not (isinstance(v, dict) and v):
        return False
    strat = next(iter(v.values()))
    t = strat.get("times") if isinstance(strat, dict) else None
    return isinstance(t, dict) and any(r in t for r in _RANKS)


def extract_standards_blob(js_text: str) -> dict:
    """Find the JSON.parse('...') literal holding the rank standards (a dict whose
    values are {strat: {'times': {...}}}) and return the parsed object."""
    for b in _all_blobs(js_text):
        if _is_standards(b):
            return b
    raise LookupError("standards blob not found in chunk")


def extract_catalog_blob(js_text: str) -> list:
    """The catalog: a list whose entries carry 'starList'."""
    for b in _all_blobs(js_text):
        if isinstance(b, list) and b and isinstance(b[0], dict) and "starList" in b[0]:
            return b
    raise LookupError("catalog blob not found in chunk")


def extract_cam_blobs(js_text: str) -> list:
    """Cam-data blobs: dicts keyed by sheet ('main'/'ext'/'beg') -> camId -> cam."""
    return [b for b in _all_blobs(js_text)
            if isinstance(b, dict) and "main" in b and ("ext" in b or "beg" in b)]


def _time_to_cs(s) -> int | None:
    """'12.60' / '1:20.63' -> centiseconds; None on missing/unparseable."""
    if not s or not isinstance(s, str):
        return None
    try:
        if ":" in s:
            m, rest = s.split(":", 1)
            return int(m) * 6000 + int(round(float(rest) * 100))
        return int(round(float(s) * 100))
    except (ValueError, TypeError):
        return None


def strat_videos(catalog_star: dict, cam_blobs: list) -> dict:
    """{strat: video url} for one catalog star. Per strat, picks the cam with
    the smallest record time that has a link; else idealLink; else any link."""
    def lookup(sheet, cid):
        for cb in cam_blobs:
            node = cb.get(sheet, {}).get(str(cid))
            if node:
                return node
        return None

    cams_by_strat = {}
    for setname in ("jp_set", "us_set"):
        for strat, info in (catalog_star.get(setname) or {}).items():
            cams_by_strat.setdefault(strat, []).extend(info.get("id_list") or [])

    out = {}
    for strat, refs in cams_by_strat.items():
        best = None            # (record_cs, link)
        ideal = None           # first idealLink
        anylink = None         # first link seen (last-resort fallback)
        for sheet, cid in refs:
            node = lookup(sheet, cid)
            if not node:
                continue
            link, rec = node.get("link"), _time_to_cs(node.get("record"))
            if link and rec is not None and (best is None or rec < best[0]):
                best = (rec, link)
            if link and anylink is None:
                anylink = link
            if node.get("idealLink") and ideal is None:
                ideal = node["idealLink"]
        url = (best[1] if best else None) or ideal or anylink
        if url:
            out[strat] = url
    return out


def build_seed(parsed: dict, catalog=None, cams=None, jp_deltas=None) -> dict:
    cat_by_stage = {i: {s["id"]: s for s in (st or {}).get("starList", [])}
                    for i, st in enumerate(catalog or [])}
    entities = {}
    for key, ladders in parsed.items():
        ek = key_to_entity(key)
        if ek is None:
            continue
        clock = "rta" if ek.startswith("segment:") else "igt"
        ent = {"clock": clock, "strategies": ladders}
        if jp_deltas and jp_deltas.get(key):
            ent["jp_strategies"] = jp_deltas[key]
        if catalog and cams:
            stage, _, starkey = key.partition("_")
            star = cat_by_stage.get(int(stage), {}).get(starkey) if stage.isdigit() else None
            if star:
                vids = {s: u for s, u in strat_videos(star, cams).items() if s in ladders}
                if vids:
                    ent["videos"] = vids
        entities[ek] = ent
    for seg_id, strategies in DEFAULT_SEGMENT_LADDERS.items():
        entities.setdefault(f"segment:{seg_id}", {"clock": "rta", "strategies": strategies})
    return {"version": 1, "entities": entities}


def fetch_all() -> tuple:
    """Fetch the chunk once; return (standards_blob, catalog, cam_blobs)."""
    base = "https://sm64-xcams.netlify.app"
    page = urllib.request.urlopen(base + "/beta", timeout=30).read().decode("utf-8", "replace")
    chunks = sorted(set(re.findall(r"/_next/static/chunks/[\w./-]+\.js", page)))
    for path in chunks:
        js = urllib.request.urlopen(base + path, timeout=30).read().decode("utf-8", "replace")
        if "Grandmaster" in js and "starList" in js:
            return extract_standards_blob(js), extract_catalog_blob(js), extract_cam_blobs(js)
    raise LookupError("could not locate standards chunk")


def main() -> None:
    standards, catalog, cams = fetch_all()
    seed = build_seed(parse_standards(standards), catalog, cams,
                      jp_deltas=parse_jp_deltas(standards))
    out = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
           / "data" / "rank_standards.seed.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seed, indent=1))
    n_vid = sum(len(e.get("videos", {})) for e in seed["entities"].values())
    n_jp = sum(len(e.get("jp_strategies", {})) for e in seed["entities"].values())
    print(f"wrote {out} ({len(seed['entities'])} entities, {n_vid} videos, {n_jp} jp-delta strats)")


if __name__ == "__main__":
    main()

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
from sm64_events.memory.addresses import star_name  # noqa: E402
from sm64_events.ranks.standards import entity_key, qualify  # noqa: E402

SEED_VERSION = 5  # bump whenever the bundled seed should push to existing installs

# A main course's 100-coin key is "<stage>_100c<N>", N being the 1-BASED exit
# star the run ends on: "3_100c1" is CCM's 100-coin run ended on Slip Slidin'
# Away, "3_100c3" the same run ended on the Big Penguin Race. Every one of
# these was dropped from the seed until 2026-08-03 — `key_to_entity` read the
# star part with `str.isdigit()` and returned None — so the 100-coin star had
# no rank standards at all while being fully practiced.
_HUNDRED_COIN = re.compile(r"^100c(\d+)$")
# The 100-coin star is star 6 on every main course (addresses.star_count).
_HUNDRED_COIN_STAR = 6

# Closed vocabulary — update if xcams adds tiers (order = fastest to slowest).
_RANKS = ["Mario", "Grandmaster", "Master", "Diamond", "Platinum",
          "Gold", "Silver", "Bronze", "Iron"]

_SECRET = {"wc": 21, "vc": 22, "mc": 20, "aqua": 24, "wmotr": 23, "pss": 19}
_BOWSER = {"1n": 5, "2n": 6, "3n": 7, "1x": 8, "2x": 9, "3x": 10}  # No Reds=pipe, Battle=Bowser
# Reds is NOT a segment — it is the Bowser course's 8-red-coin star (star 0 of
# courses 16/17/18), the target the stage banner sets for "Reds". Leaving these
# keys unmapped silently dropped every Bowser reds ladder from the seed
# (user-reported 2026-07-23); tests pin the bundled seed's Bowser coverage.
_BOWSER_REDS = {"1r": 16, "2r": 17, "3r": 18}

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


# Known upstream data bugs, corrected at scrape time: key -> strat -> rank ->
# (value as published, corrected value), both in seconds. The published value
# is part of the key so the fixup DISARMS ITSELF the moment xcams edits that
# cell — we never silently overwrite a number they have since changed.
#
# This lives here, not in the user's standards file, because a hand edit to a
# SEEDED cutoff is overwritten by the next seed reconcile (ranks/standards.py
# _reconcile: the bundled seed wins for community data).
_TIME_FIXUPS = {
    # bow_2r "No Early Ellies": the top three cutoffs are stored with the
    # minute dropped (1000/1003/1006 cs instead of 7000/7003/7006). Evidence:
    # the ladder continues Diamond 1:10.76 with 0.03 spacing above it, and the
    # (Pipe) variant sits a constant +8.30 s from (Star) at every OTHER tier —
    # which only holds if these three are 1:10.0x / 1:18.3x. xcams renders the
    # same broken values on its own page (human-confirmed 2026-07-23), so this
    # is their data, not our parse.
    "16_2r": {
        "No Early Ellies (Star)": {"Mario": (10.00, 70.00),
                                   "Grandmaster": (10.03, 70.03),
                                   "Master": (10.06, 70.06)},
        "No Early Ellies (Pipe)": {"Mario": (18.30, 78.30),
                                   "Grandmaster": (18.33, 78.33),
                                   "Master": (18.36, 78.36)},
    },
}


def apply_fixups(parsed: dict) -> dict:
    """Apply _TIME_FIXUPS to a parsed-standards dict (new dict, inputs intact).
    A cell is rewritten only when it still holds the exact published value."""
    out = {key: {strat: dict(ladder) for strat, ladder in strats.items()}
           for key, strats in parsed.items()}
    for key, strats in _TIME_FIXUPS.items():
        for strat, cells in strats.items():
            ladder = out.get(key, {}).get(strat)
            if ladder is None:
                continue                       # upstream renamed or dropped it
            for rank, (published, corrected) in cells.items():
                if ladder.get(rank) == published:
                    ladder[rank] = corrected
    return out


def suspect_dropped_minute(parsed: dict) -> list[tuple[str, str, str]]:
    """(key, strat, rank) cells that look like a missing minute: the next
    SLOWER tier is roughly 60 s slower than this one.

    Monotonicity does NOT catch this class — "10.00" sorts happily before
    "1:10.76" — so the ladder looks fine while its top tiers are unreachable.
    main() prints these; the tests pin the bundled seed's list empty (post
    fixup), so the next season's scrape reports a new one instead of shipping
    it silently."""
    out = []
    for key, strats in parsed.items():
        for strat, ladder in strats.items():
            present = [(r, ladder[r]) for r in _RANKS if r in ladder]
            for (rank, fast), (_, slow) in zip(present, present[1:]):
                if slow - fast > 50 and abs((fast + 60) - slow) < 10:
                    out.append((key, strat, rank))
    return out


def hundred_coin_exit(key: str) -> int | None:
    """The 0-based exit star a "<stage>_100c<N>" key ends on, else None.
    Only main courses (stages 0-14) have one."""
    stage, _, star = key.partition("_")
    if not stage.isdigit() or not 0 <= int(stage) <= 14:
        return None
    match = _HUNDRED_COIN.match(star)
    return int(match.group(1)) - 1 if match else None


def key_to_entity(key: str) -> str | None:
    stage, _, star = key.partition("_")
    if not stage.isdigit():
        return None
    s = int(stage)
    if 0 <= s <= 14:
        if hundred_coin_exit(key) is not None:
            return entity_key(s + 1, _HUNDRED_COIN_STAR)
        return entity_key(s + 1, int(star) - 1) if star.isdigit() else None
    if s == 15:
        c = _SECRET.get(star)
        return entity_key(c, 0) if c else None
    if s == 16:
        course = _BOWSER_REDS.get(star)
        if course:
            return entity_key(course, 0)
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


def strat_clips(catalog_star: dict, cam_blobs: list) -> dict:
    """{strat: [[record_cs, url], ...]} for one catalog star — EVERY cam that has
    both a parseable record AND a link, fastest first, deduped by url (a repeated
    link keeps its fastest record). Feeds the per-rank band resolution
    (classify.resolve_cutoff_videos); strat_videos still owns the single primary/
    fallback url (which may be an untimed idealLink that has no place here)."""
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
        best_by_link = {}                      # url -> fastest record_cs
        for sheet, cid in refs:
            node = lookup(sheet, cid)
            if not node:
                continue
            link, rec = node.get("link"), _time_to_cs(node.get("record"))
            if link and rec is not None and rec < best_by_link.get(link, 1 << 30):
                best_by_link[link] = rec
        rows = sorted([[rec, link] for link, rec in best_by_link.items()])
        if rows:
            out[strat] = rows
    return out


def variant_label(key: str, exit_star: int, catalog_star=None) -> str:
    """The exit-star variant's display label — xcams' own short name for the
    100-coin entry ("100c + Slide", "100c + KtQ"), which is what the community
    calls it, falling back to our star registry when the catalog is absent."""
    short = ((catalog_star or {}).get("info") or {}).get("short")
    if short:
        return short
    stage = int(key.partition("_")[0])
    return f"100c + {star_name(stage + 1, exit_star)}"


def build_seed(parsed: dict, catalog=None, cams=None, jp_deltas=None) -> dict:
    parsed = apply_fixups(parsed)
    cat_by_stage = {i: {s["id"]: s for s in (st or {}).get("starList", [])}
                    for i, st in enumerate(catalog or [])}
    entities = {}
    for key, ladders in parsed.items():
        ek = key_to_entity(key)
        if ek is None:
            continue
        stage, _, starkey = key.partition("_")
        star = cat_by_stage.get(int(stage), {}).get(starkey) if stage.isdigit() else None
        # SEVERAL xcams keys map to ONE entity for a 100-coin star (one per
        # exit-star variant), so this loop MERGES. It assigned
        # `entities[ek] = ent` until 2026-08-03, which for CCM/WDW/THI/RR would
        # silently keep whichever variant the blob listed last.
        exit_star = hundred_coin_exit(key)
        clock = "rta" if ek.startswith("segment:") else "igt"
        ent = entities.setdefault(ek, {"clock": clock, "strategies": {}})
        if exit_star is None:
            rename = {s: s for s in ladders}                # identity
        else:
            label = variant_label(key, exit_star, star)
            known = ent.setdefault("exit_variants", {})
            if known.get(label, exit_star) != exit_star:
                raise ValueError(f"{ek}: label {label!r} claims two exit stars")
            known[label] = exit_star
            rename = {s: qualify(label, s) for s in ladders}
        for was, now in rename.items():
            if now in ent["strategies"]:
                raise ValueError(f"{ek}: two ladders would be stored as {now!r}")
            ent["strategies"][now] = ladders[was]
        if jp_deltas and jp_deltas.get(key):
            ent.setdefault("jp_strategies", {}).update(
                {rename[s]: d for s, d in jp_deltas[key].items() if s in rename})
        if catalog and cams and star:
            vids = {rename[s]: u for s, u in strat_videos(star, cams).items()
                    if s in rename}
            if vids:
                ent.setdefault("videos", {}).update(vids)
            clips = {rename[s]: c for s, c in strat_clips(star, cams).items()
                     if s in rename}
            if clips:
                ent.setdefault("clips", {}).update(clips)
    for seg_id, strategies in DEFAULT_SEGMENT_LADDERS.items():
        entities.setdefault(f"segment:{seg_id}", {"clock": "rta", "strategies": strategies})
    return {"version": SEED_VERSION, "entities": entities}


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
    parsed = parse_standards(standards)
    seed = build_seed(parsed, catalog, cams,
                      jp_deltas=parse_jp_deltas(standards))
    for key, strat, rank in suspect_dropped_minute(apply_fixups(parsed)):
        print(f"WARNING: {key} / {strat} / {rank} looks like a dropped minute "
              f"(next tier ~60 s slower) — add a _TIME_FIXUPS entry if so")
    out = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
           / "data" / "rank_standards.seed.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seed, indent=1))
    n_vid = sum(len(e.get("videos", {})) for e in seed["entities"].values())
    n_clip = sum(len(rows) for e in seed["entities"].values()
                 for rows in e.get("clips", {}).values())
    n_jp = sum(len(e.get("jp_strategies", {})) for e in seed["entities"].values())
    print(f"wrote {out} ({len(seed['entities'])} entities, {n_vid} videos, "
          f"{n_clip} clips, {n_jp} jp-delta strats)")


if __name__ == "__main__":
    main()

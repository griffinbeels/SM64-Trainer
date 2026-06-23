# RANK-VIDEOS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a per-strategy "fastest-time proof video" link to rank standards, and render each strategy column header in the standards table as a hyperlink to that video.

**Architecture:** Purely additive to the existing ranks feature. The scraper gains a strat→video join (from the xcams catalog's `id_list` + the cam-data blobs' `record`/`link`), emitting a `videos: {strat: url}` map per entity in the seed (sibling to `strategies`). The store exposes `videos()`/`video_for()`; the GET endpoint returns `videos`; the UI standards-table header links to it. Ranking logic (classify/banner/medals/routes) is untouched.

**Tech Stack:** Python 3.12 via uv; Preact-via-htm UI (no build step).

## Global Constraints
- `uv run pytest -q` must pass (baseline on this branch: 940).
- Additive only: do NOT change the `strategies` ladder shape or any classification code. `videos` is a new sibling key per entity.
- Video selection: per strat, pick the cam with the smallest parseable `record` time that HAS a `link`; fall back to `idealLink`; then to any `link`. None → no entry for that strat (header stays plain text).
- Entity identity unchanged: `star:<course>:<star>` / `segment:<id>`.
- Strat names in the standards blob match the catalog's set strat names (verified).
- Use uv (never pip). UI: ES modules, htm; verify with `node --check`.

## File Structure
- Modify: `tools/scrape_ranks.py` (join + extraction + build_seed + main), `src/sm64_events/ranks/standards.py` (accessors), `src/sm64_events/server/ranks_api.py` (GET), `src/sm64_events/ui/components/standards.js` (header link).
- Regenerate: `src/sm64_events/data/rank_standards.seed.json`.
- Tests: `tests/test_scrape_ranks.py`, `tests/test_ranks_standards.py`, `tests/test_ranks_api.py`.

---

## Task 1: Scraper — strat→video join + seed regeneration

**Files:**
- Modify: `tools/scrape_ranks.py`
- Test: `tests/test_scrape_ranks.py` (append)
- Regenerate: `src/sm64_events/data/rank_standards.seed.json`

**Interfaces:**
- Produces: `_time_to_cs(s)->int|None`; `strat_videos(catalog_star: dict, cam_blobs: list)->dict[str,str]`; `extract_catalog_blob(js)->list`; `extract_cam_blobs(js)->list`; `fetch_all()->tuple[dict, list, list]` (standards, catalog, cams); `build_seed(parsed, catalog=None, cams=None)` now attaches `videos`.

**Verified data shape (live 2026-06-23):** catalog is a JSON.parse blob = list of 17 stage dicts, each `{"starList": [{"id": "3", "name": ..., "jp_set": {strat: {"id_list": [["ext",182],...]}}, "us_set": {...}}]}`. Cam data lives in JSON.parse blob(s) shaped `{"main": {camId: {"record": "12.60", "link": "https://...", "ideal": null, "idealLink": null, ...}}, "ext": {...}, "beg": {...}}`. `id_list` entries are `[sheet, camId]`. Example: ssl (stage 7) star id "3" → Nuts Pless → `[["ext",182]]` → cam ext/182 record 12.60 link `https://youtu.be/18cLwH6yEiA`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scrape_ranks.py  (append)
def test_time_to_cs():
    assert scrape._time_to_cs("12.60") == 1260
    assert scrape._time_to_cs("1:20.63") == 8063
    assert scrape._time_to_cs(None) is None
    assert scrape._time_to_cs("-") is None

_CAMS = [{"ext": {"182": {"record": "12.60", "link": "https://youtu.be/A", "ideal": None, "idealLink": None},
                  "9":   {"record": "12.40", "link": None, "ideal": "12.0", "idealLink": "https://youtu.be/IDEAL"}},
          "main": {"5": {"record": "13.00", "link": "https://youtu.be/SLOW", "ideal": None, "idealLink": None}}}]

def test_strat_videos_picks_fastest_record_with_link():
    star = {"jp_set": {"Nuts": {"id_list": [["ext", 182]]},
                        "Multi": {"id_list": [["ext", 182], ["main", 5]]}},
            "us_set": {}}
    out = scrape.strat_videos(star, _CAMS)
    assert out["Nuts"] == "https://youtu.be/A"
    assert out["Multi"] == "https://youtu.be/A"        # 12.60 (A) beats 13.00 (SLOW)

def test_strat_videos_falls_back_to_ideallink_then_any_link():
    star = {"jp_set": {"NoRecLink": {"id_list": [["ext", 9]]}}, "us_set": {}}
    # ext/9 has no record link but has idealLink -> use idealLink
    assert scrape.strat_videos(star, _CAMS)["NoRecLink"] == "https://youtu.be/IDEAL"

def test_build_seed_attaches_videos():
    parsed = {"7_3": {"Nuts": {"Mario": 12.6}}}
    catalog = [None]*7 + [{"starList": [{"id": "3", "name": "x",
                          "jp_set": {"Nuts": {"id_list": [["ext", 182]]}}, "us_set": {}}]}]
    seed = scrape.build_seed(parsed, catalog, _CAMS)
    assert seed["entities"]["star:8:2"]["videos"]["Nuts"] == "https://youtu.be/A"

def test_build_seed_without_catalog_omits_videos():
    seed = scrape.build_seed({"7_3": {"Nuts": {"Mario": 12.6}}})
    assert "videos" not in seed["entities"]["star:8:2"]
```

- [ ] **Step 2: Run — expect FAIL** (`uv run pytest tests/test_scrape_ranks.py -q`)

- [ ] **Step 3: Implement** in `tools/scrape_ranks.py`

Add a shared blob walker + classifiers + the join, and extend `build_seed`/`fetch`/`main`. Refactor `extract_standards_blob` to reuse the shared walker (keep its existing behavior + tests green).

```python
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
        anylink = None         # first link with no usable record
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
```

Refactor `extract_standards_blob` to use the walker:

```python
def extract_standards_blob(js_text: str) -> dict:
    for b in _all_blobs(js_text):
        if _is_standards(b):
            return b
    raise LookupError("standards blob not found in chunk")
```

Extend `build_seed`:

```python
def build_seed(parsed: dict, catalog=None, cams=None) -> dict:
    cat_by_stage = {i: {s["id"]: s for s in (st or {}).get("starList", [])}
                    for i, st in enumerate(catalog or [])}
    entities = {}
    for key, ladders in parsed.items():
        ek = key_to_entity(key)
        if ek is None:
            continue
        clock = "rta" if ek.startswith("segment:") else "igt"
        ent = {"clock": clock, "strategies": ladders}
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
```

Add `fetch_all` and update `main` to use it:

```python
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
```

Change `main()` to:

```python
def main() -> None:
    standards, catalog, cams = fetch_all()
    seed = build_seed(parse_standards(standards), catalog, cams)
    out = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
           / "data" / "rank_standards.seed.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seed, indent=1))
    n_vid = sum(len(e.get("videos", {})) for e in seed["entities"].values())
    print(f"wrote {out} ({len(seed['entities'])} entities, {n_vid} videos)")
```

(Keep `fetch_standards()` if it's still referenced by tests; otherwise it may remain unused — leave it.)

- [ ] **Step 4: Run tests — expect PASS** (`uv run pytest tests/test_scrape_ranks.py -q`)

- [ ] **Step 5: Regenerate the seed (live).**
```bash
uv run python tools/scrape_ranks.py
```
Confirm the output reports a non-zero video count and spot-check:
```bash
python -c "import json; d=json.load(open('src/sm64_events/data/rank_standards.seed.json')); v=d['entities']['star:8:2']['videos']; print('Nuts Pless ->', v.get('Nuts Pless'))"
```
Expected: `Nuts Pless -> https://youtu.be/18cLwH6yEiA` (Sadr's 12.60). If the live fetch fails, build offline from the committed `tests/fixtures/xcams_standards.json` is NOT possible here (videos need the catalog+cam blobs, which aren't in that fixture) — re-run the live fetch.

- [ ] **Step 6: Full suite + commit**
```bash
uv run pytest -q
git add tools/scrape_ranks.py tests/test_scrape_ranks.py src/sm64_events/data/rank_standards.seed.json
git commit -m "ranks: scrape per-strat fastest-time video link; emit videos[] in seed"
```

---

## Task 2: Store accessors + API GET returns videos

**Files:**
- Modify: `src/sm64_events/ranks/standards.py`, `src/sm64_events/server/ranks_api.py`
- Test: `tests/test_ranks_standards.py`, `tests/test_ranks_api.py` (append)

**Interfaces:**
- Produces: `RankStandards.videos(ek)->dict`, `RankStandards.video_for(ek, strat)->str|None`; GET `/api/ranks/standards?entity=` response gains `"videos"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ranks_standards.py  (append)
def test_videos_accessors(tmp_path):
    import json
    p = tmp_path / "rs.json"
    p.write_text(json.dumps({"version": 1, "entities": {
        "star:8:2": {"clock": "igt", "strategies": {"Nuts": {"Mario": 12.6}},
                     "videos": {"Nuts": "https://youtu.be/A"}}}}))
    s = RankStandards(p); s.load()
    assert s.videos("star:8:2") == {"Nuts": "https://youtu.be/A"}
    assert s.video_for("star:8:2", "Nuts") == "https://youtu.be/A"
    assert s.video_for("star:8:2", "Missing") is None
    assert s.videos("segment:99") == {}        # absent entity -> empty
```

```python
# tests/test_ranks_api.py  (append; reuse make_client)
def test_get_standards_includes_videos(tmp_path):
    import json
    client, svc = make_client(tmp_path)
    # seed a video directly into the store
    svc.ranks._data["entities"]["star:8:2"] = {
        "clock": "igt", "strategies": {"Nuts": {"Mario": 12.6}},
        "videos": {"Nuts": "https://youtu.be/A"}}
    with client:
        r = client.get("/api/ranks/standards", params={"entity": "star:8:2"})
        assert r.status_code == 200
        assert r.json()["videos"] == {"Nuts": "https://youtu.be/A"}
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement.** In `ranks/standards.py` (beside `ladders`/`strategies`):

```python
    def videos(self, ek) -> dict:
        return self._entity(ek).get("videos", {})

    def video_for(self, ek, strat) -> str | None:
        return self.videos(ek).get(strat)
```

In `server/ranks_api.py`, the per-entity GET branch — add `videos`:

```python
        return {"entity": entity, "clock": service.ranks.clock_for(entity),
                "strategies": service.ranks.ladders(entity),
                "videos": service.ranks.videos(entity)}
```

- [ ] **Step 4: Run tests — expect PASS** (both files)

- [ ] **Step 5: Full suite + commit**
```bash
uv run pytest -q
git add src/sm64_events/ranks/standards.py src/sm64_events/server/ranks_api.py tests/test_ranks_standards.py tests/test_ranks_api.py
git commit -m "ranks: store videos() accessors + GET returns per-strat videos"
```

---

## Task 3: UI — strat-header hyperlink in the standards table

**Files:**
- Modify: `src/sm64_events/ui/components/standards.js`
- Verify: `node --check` + frontend smoke (later)

**Interfaces:**
- Consumes: the GET response's new `videos` map (`data.videos[strat]`).

- [ ] **Step 1: Render the header as a link when a video exists.** In `standards.js`, find the strategy-column `<th>` rendering (the `strats.map(...)` in the table header — currently renders `${s}` plus the edit-mode `×` remove button). Wrap the strat name in a link when `data.videos` has it:

```javascript
        ${strats.map((s) => html`<th class=${s === activeStrat ? "col-active" : ""}>${
          data.videos && data.videos[s]
            ? html`<a href=${data.videos[s]} target="_blank" rel="noopener"
                    title="fastest-time video">${s}</a>`
            : s
        }${editing ? html` <button class="candx" title="remove strategy" onclick=${() => delStrat(s)}>×</button>` : ""}</th>`)}
```

(Match the EXACT current header expression in the file — it already contains the `${editing ? ... delStrat ...}` part; only the `${s}` strat-name portion changes to the conditional link. Keep everything else identical.)

- [ ] **Step 2: Verify**
```bash
node --check src/sm64_events/ui/components/standards.js
uv run pytest -q        # unaffected
```

- [ ] **Step 3: Commit**
```bash
git add src/sm64_events/ui/components/standards.js
git commit -m "ranks(ui): link strat-column headers to the fastest-time video"
```

---

## Self-Review
- Scrape join (catalog id_list + cam record/link) → Task 1 (strat_videos, unit-tested incl. fastest/fallback). ✓
- Seed carries videos per entity → Task 1 build_seed + live regen (spot-checked ssl_3). ✓
- Store accessors + GET returns videos → Task 2. ✓
- Header hyperlink (header-only scope) → Task 3. ✓
- Additive: no change to classify/banner/medals/routes; `videos` filtered to strats present in `strategies`. ✓
- Fallback order record-link → idealLink → any-link matches the agreed decision. ✓

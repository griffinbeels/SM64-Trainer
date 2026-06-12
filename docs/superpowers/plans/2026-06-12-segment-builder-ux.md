# Segment Builder Sentence-Style Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render segment-builder trigger rows as readable sentences ("You enter level [Castle Inside] coming from [Castle Grounds]") driven by templates in the trigger registry, fix the row overflow, fix the optional-area-clear bug, and turn course/star params into dependent dropdowns.

**Architecture:** `TriggerType`/`GuardType` in `tracking/segments.py` gain a `template` field with `{param}` placeholders; `vocab()` serializes it plus new `courses`/`stars` enums. `ClauseRow` in `segments.js` splits the template and interleaves muted connector words with the existing `ParamInput`s — the builder stays 100% vocab-driven (adding a trigger type remains one registry row, zero JS changes). Spec: `docs/superpowers/specs/2026-06-12-segment-builder-ux-design.md`.

**Tech Stack:** Python 3.12 via uv, FastAPI, pytest; Preact + htm (vendored, no build step); UI served per request — edit + refresh, no server restart.

---

## Shared-checkout protocol (read first)

Another session is actively editing this checkout on `master` — `README.md` and `tests/test_api.py` currently carry **uncommitted foreign changes**. Execute this plan in an isolated worktree (superpowers:using-git-worktrees) branched from committed HEAD so those edits stay behind. If executing in the main checkout instead: run `git status --porcelain` before every commit, stage only this plan's files, and if a file you must edit (README.md, tests/test_api.py) shows foreign modifications, STOP and surface to the user rather than committing mixed work.

## File structure

| File | Responsibility in this plan |
|---|---|
| `src/sm64_events/tracking/segments.py` | Registry: `template` field on `TriggerType`/`GuardType`, template values, `vocab()` gains `template`/`courses`/`stars` |
| `src/sm64_events/ui/components/segments.js` | `ParamInput` kind branches (area fix, course, star); `ClauseRow` template rendering |
| `src/sm64_events/ui/index.html` | CSS: `.segclause` wrap, select max-width, new `.segword` |
| `tests/test_segments.py` | Registry template tests + vocab enum tests |
| `tests/test_api.py` | Vocab endpoint shape extension (foreign-edit caution above) |
| `README.md` | Vocab payload line 120 (foreign-edit caution above) |

No new files. Tests-first for Python; the repo has no JS unit harness — UI tasks verify via the browser smoke test in Task 6 (established pattern: frontend-smoke-test skill is the mandatory gate).

---

### Task 1: Registry templates

**Files:**
- Modify: `src/sm64_events/tracking/segments.py` (dataclasses ~line 112+186, registry rows ~125-207, `vocab()` ~249)
- Test: `tests/test_segments.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_segments.py` (module imports `vocab` already; add `re`, `TRIGGERS`, `GUARDS`):

```python
import re

from sm64_events.tracking.segments import GUARDS, TRIGGERS


def test_every_trigger_and_guard_template_matches_its_params():
    """A template typo must fail CI, not render a broken builder row."""
    for reg in (TRIGGERS, GUARDS):
        for t in reg.values():
            assert t.template.strip(), f"{t.key}: empty template"
            placeholders = set(re.findall(r"\{(\w+)\}", t.template))
            assert placeholders == set(t.params), (
                f"{t.key}: template placeholders {placeholders}"
                f" != params {set(t.params)}")


def test_vocab_serializes_templates():
    v = vocab()
    by_key = {t["key"]: t for t in v["triggers"]}
    assert by_key["level_enter"]["template"] == "{to} coming from {from}"
    assert by_key["attempt_anchor"]["label"] == (
        "Practice reset / savestate load")  # "in level" moved into template
    assert all("template" in t for t in v["triggers"] + v["guards"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_segments.py -q`
Expected: 2 new tests FAIL with `AttributeError: ... no attribute 'template'`; existing tests PASS.

- [ ] **Step 3: Add the `template` field to both dataclasses**

In `src/sm64_events/tracking/segments.py`, the field goes between `params` and the callable (rows pass it positionally as the 4th argument):

```python
@dataclass(frozen=True)
class TriggerType:
    key: str
    label: str
    params: dict  # name -> {"kind": "level"|"area"|"course"|"star"|"int", "required": bool}
    template: str  # sentence after the type label: "{to} coming from {from}"
    match: Callable[[dict, object, MatchContext], bool]
```

```python
@dataclass(frozen=True)
class GuardType:
    key: str
    label: str
    params: dict
    template: str
    check: Callable[[dict, MatchContext], bool]
```

- [ ] **Step 4: Insert the template into every registry row**

Each row gets its template string on a new line directly after the params dict (4th positional arg). Exact insertions — the anchor is each row's params dict closing `},`:

| Row | Insert after params dict |
|---|---|
| `level_enter` | `"{to} coming from {from}",` |
| `level_exit` | `"{from} going to {to}",` |
| `area_enter` | `"{area} of {level}",` |
| `warp_entered` | `"in {level}",` |
| `key_grabbed` | `"in {level}",` |
| `star_grabbed` | `"in {course}, star {star}",` |
| `spawned` | `"in {level}",` |
| `attempt_anchor` | `"in {level}, area {area}",` |
| `prev_level` (guard) | `"{level}",` |
| `star_count_min` (guard) | `"{n}",` |
| `star_count_max` (guard) | `"{n}",` |

Example — `level_enter` row becomes:

```python
    TriggerType("level_enter", "You enter level",
                {"to": {"kind": "level", "required": True},
                 "from": {"kind": "level", "required": False}},
                "{to} coming from {from}",
                lambda p, ev, ctx: ev.type == "level_changed" and _real_edge(ev)
                and ev.payload["to"] == p["to"]
                and (p.get("from") is None or ev.payload["from"] == p["from"])),
```

Also shorten the `attempt_anchor` label (the template now carries "in level"):

```python
    TriggerType("attempt_anchor", "Practice reset / savestate load",
                {"level": {"kind": "level", "required": True},
                 "area": {"kind": "area", "required": False}},
                "in {level}, area {area}",
```

(keep the row's existing comment block and lambda unchanged).

- [ ] **Step 5: Serialize `template` in `vocab()`**

```python
        "triggers": [{"key": t.key, "label": t.label, "params": t.params,
                      "template": t.template} for t in TRIGGERS.values()],
        "guards": [{"key": g.key, "label": g.label, "params": g.params,
                    "template": g.template} for g in GUARDS.values()],
```

- [ ] **Step 6: Run the test file, then the full suite**

Run: `uv run pytest tests/test_segments.py -q` → all PASS.
Run: `uv run pytest -q` → all PASS (nothing else constructs `TriggerType`/`GuardType` positionally).

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/tracking/segments.py tests/test_segments.py
git commit -m "feat: sentence templates in the trigger registry" -m "Builder rows were unlabeled dropdowns in params order - 'You enter level [X] [Y]' gave no clue which is origin vs destination. Templates keep the one-registry property: adding a trigger type still needs zero JS changes."
```

---

### Task 2: vocab course/star enums

**Files:**
- Modify: `src/sm64_events/tracking/segments.py` (imports ~line 75, `vocab()` ~249)
- Test: `tests/test_segments.py`, `tests/test_api.py` (**foreign-edit caution** — see protocol)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_segments.py`:

```python
def test_vocab_course_and_star_enums():
    v = vocab()
    assert v["courses"]["2"] == "Whomp's Fortress"
    assert v["stars"]["2"][2] == "Shoot into the Wild Blue"
    assert v["stars"]["1"][6] == "100 Coins"    # main courses: 100-coin star at star_id 6
    assert len(v["stars"]["1"]) == 7
    assert v["stars"]["16"] == ["8 Red Coins"]  # Bowser course: one star
    assert v["stars"]["0"] == []                # Castle Secret: no named stars
```

In `tests/test_api.py`, extend `test_vocab_endpoint_shape` (line ~444) — the assert becomes:

```python
        v = client.get("/api/segments/vocab").json()
        assert "triggers" in v and "levels" in v and "guards" in v
        assert "courses" in v and "stars" in v
        assert all("template" in t for t in v["triggers"] + v["guards"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_segments.py::test_vocab_course_and_star_enums tests/test_api.py::test_vocab_endpoint_shape -q`
Expected: both FAIL with `KeyError: 'courses'`.

- [ ] **Step 3: Implement in `vocab()`**

Extend the addresses import (line ~75):

```python
from sm64_events.memory.addresses import (CASTLE_AREA_NAMES, COURSE_NAMES,
                                          DOOR_ACTIONS, LEVEL_NAMES,
                                          STAR_NAMES, star_name)
```

Add to the dict returned by `vocab()`:

```python
        "courses": {str(k): v for k, v in COURSE_NAMES.items()},
        # star_id order, via star_name() so courses 1-15 include the
        # 100-coin star at star_id 6 (star_name owns that rule)
        "stars": {str(cid): [star_name(cid, s)
                             for s in range(7 if 1 <= cid <= 15
                                            else len(STAR_NAMES.get(cid, ())))]
                  for cid in COURSE_NAMES},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_segments.py tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/segments.py tests/test_segments.py tests/test_api.py
git commit -m "feat: vocab ships course/star name enums" -m "star_grabbed params rendered as bare number inputs because the vocab had no names to offer. COURSE_NAMES/STAR_NAMES already exist in addresses.py; star_name() supplies the 100-coin star convention."
```

---

### Task 3: ParamInput kind branches (area-clear bug fix, course/star dropdowns)

**Files:**
- Modify: `src/sm64_events/ui/components/segments.js:12-32` (`ParamInput`) plus the one `ParamInput` call site in `ClauseRow` (~line 42)

No JS unit harness — verification is Task 6's smoke test. The page is served per request: edit + refresh.

- [ ] **Step 1: Replace `ParamInput` wholesale**

```js
function ParamInput({ schema, name, value, vocab, clause, onChange }) {
  const numOrNull = (s) => (s === "" ? null : Number(s));
  const dropdown = (entries, anyLabel, pickLabel) => html`<select
      value=${value ?? ""} onchange=${(e) => onChange(numOrNull(e.target.value))}>
    <option value="">${schema.required ? pickLabel : anyLabel}</option>
    ${entries.map(([id, n]) => html`<option value=${id}>${n}</option>`)}
  </select>`;
  if (schema.kind === "level")
    return dropdown(Object.entries(vocab.levels), "(any level)", "— pick level —");
  if (schema.kind === "area")
    return dropdown(Object.entries(vocab.castle_areas), "(any area)", "— pick area —");
  if (schema.kind === "course")
    return dropdown(Object.entries(vocab.courses), "(any course)", "— pick course —");
  if (schema.kind === "star") {
    // dependent on the sibling course param: no course (or "any course")
    // implies any star, so the selector is disabled until a course is picked
    const names = vocab.stars[String(clause.course)] || [];
    return html`<select value=${value ?? ""} disabled=${clause.course == null}
        onchange=${(e) => onChange(numOrNull(e.target.value))}>
      <option value="">(any star)</option>
      ${names.map((n, i) => html`<option value=${i}>${n}</option>`)}
    </select>`;
  }
  return html`<input type="number" style="width:5rem" value=${value ?? ""}
      placeholder=${name}
      onchange=${(e) => onChange(numOrNull(e.target.value))} />`;
}
```

This fixes the area-clear bug by construction: every dropdown maps `""` through `numOrNull` to `null` (the old area branch did `Number("")` = `0`, silently scoping the clause to area 0).

- [ ] **Step 2: Pass `clause` at the call site**

In `ClauseRow`, the `ParamInput` invocation gains `clause=${clause}`:

```js
      <${ParamInput} schema=${schema} name=${name} vocab=${vocab}
        clause=${clause} value=${clause[name]}
        onChange=${(v) => onChange({ ...clause, [name]: v })} />`)}
```

(Task 4 restructures this call site again — apply this step anyway so the file is consistent if Task 4 is reviewed separately.)

- [ ] **Step 3: Quick browser sanity check**

Run (if not already running): `uv run uvicorn sm64_events.main:app --host 127.0.0.1 --port 8064` from repo root. Load `http://127.0.0.1:8064`, Segments tab, edit any definition: dropdowns render, no console errors. (Full behavior checks in Task 6.)

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/ui/components/segments.js
git commit -m "fix: ParamInput honors required for every kind; course/star become dropdowns" -m "Clearing an optional area sent Number('')=0 - silently scoping the clause to area 0 instead of any-area. Star selector is dependent: disabled until a specific course is picked (any course implies any star)."
```

---

### Task 4: ClauseRow renders the sentence template

**Files:**
- Modify: `src/sm64_events/ui/components/segments.js:34-47` (`ClauseRow`)

- [ ] **Step 1: Replace `ClauseRow` wholesale**

```js
function ClauseRow({ clause, types, vocab, onChange, onRemove }) {
  const spec = types.find((t) => t.key === clause.type) || types[0];
  const setParam = (pname, v) => {
    const next = { ...clause, [pname]: v };
    // a star id is meaningless outside its course — clear it on course change
    if (pname === "course" && "star" in spec.params) next.star = null;
    onChange(next);
  };
  const param = (pname) => html`<${ParamInput} schema=${spec.params[pname]}
      name=${pname} vocab=${vocab} clause=${clause} value=${clause[pname]}
      onChange=${(v) => setParam(pname, v)} />`;
  // "{to} coming from {from}" → inputs interleaved with muted words.
  // Params a template forgets to mention render appended — the registry
  // test makes that unreachable; this keeps a bad vocab usable, not blank.
  const mentioned = new Set();
  const rendered = (spec.template || "").split(/(\{\w+\})/).map((tok) => {
    const m = /^\{(\w+)\}$/.exec(tok);
    if (m && spec.params[m[1]]) { mentioned.add(m[1]); return param(m[1]); }
    const word = tok.trim();
    return word ? html`<span class="segword">${word}</span>` : null;
  });
  const extras = Object.keys(spec.params).filter((p) => !mentioned.has(p));
  return html`<div class="segclause">
    <select value=${clause.type}
        onchange=${(e) => onChange({ type: e.target.value })}>
      ${types.map((t) => html`<option value=${t.key}>${t.label}</option>`)}
    </select>
    ${rendered}
    ${extras.map(param)}
    <button onclick=${onRemove}>✕</button>
  </div>`;
}
```

Behavior notes for the implementer:
- The type-change handler (`onChange({ type: ... })`) still wipes all params — switching trigger type must not leak the old type's params (the server rejects unknown params with 409).
- `setParam`'s star reset only fires when the spec actually has a `star` param, so non-star clauses never gain a stray `star: null` key (which the server would reject).

- [ ] **Step 2: Browser sanity check**

Refresh `http://127.0.0.1:8064`, Segments tab, edit LBLJ. Expect: "You enter level [Castle Inside] coming from [Castle Grounds]" and "Practice reset / savestate load in [Castle Inside] , area [Lobby]". No console errors.

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/segments.js
git commit -m "feat: builder rows read as sentences from registry templates" -m "ClauseRow interleaves the vocab template's connector words with the param inputs, so directional triggers read 'to X coming from Y' instead of two anonymous dropdowns."
```

---

### Task 5: CSS — wrap rows, contain selects, style connector words

**Files:**
- Modify: `src/sm64_events/ui/index.html:33` (`.segclause`) + adjacent new rules

- [ ] **Step 1: Update the stylesheet**

Replace line 33:

```css
  .segclause { display: flex; gap: .4rem; margin: .2rem 0; }
```

with:

```css
  .segclause { display: flex; flex-wrap: wrap; gap: .4rem; margin: .2rem 0; align-items: center; }
  .segclause select { max-width: 100%; }
  .segword { color: #6c7686; font-size: .85em; }
```

(`#6c7686` matches the existing `.meta` muted color, line 39.)

- [ ] **Step 2: Browser check**

Refresh, narrow the window until level names crowd the row: inputs wrap to the next line; the ✕ button stays inside the pane border.

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/index.html
git commit -m "fix: builder clause rows wrap instead of bleeding past the pane" -m "Long level names forced the flex row wider than its container. segword styles the new sentence connector words to match .meta."
```

---

### Task 6: README, full suite, smoke test, human audit

**Files:**
- Modify: `README.md:120` (**foreign-edit caution** — see protocol)

- [ ] **Step 1: Update the vocab line in README**

Line 120's payload shape becomes:

```
| `GET /api/segments/vocab` | Trigger vocabulary for the builder GUI: `{triggers, guards, levels, castle_areas, courses, stars}`; each trigger/guard carries a sentence `template` ("{to} coming from {from}") the builder renders. Always 200 (no db dependency). |
```

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS. (Definition of done: must pass before any merge.)

- [ ] **Step 3: Browser smoke test (frontend-smoke-test skill — mandatory gate)**

With the server running, via chrome-devtools MCP against `http://127.0.0.1:8064`:

1. Segments tab → edit **LBLJ** → rows read as sentences (start: "You enter level [Castle Inside] coming from [Castle Grounds]"; anchor row shows "in [Castle Inside] , area [Lobby]").
2. Resize to ~700px wide → rows wrap; nothing crosses the pane border.
3. In the anchor row, set area to "(any area)" → Save → inspect the PUT `/api/segments/{id}` request body: `"area": null` (NOT `0`). Restore area to Lobby and save again.
4. \+ New segment → trigger "You grab a star" → star select is **disabled** showing "(any star)". Pick course "Whomp's Fortress" → star select enables and lists "Shoot into the Wild Blue" and "100 Coins". Switch course → star resets to "(any star)". Set course back to "(any course)" → star disables. Cancel (don't save).
5. Console: zero errors throughout.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: vocab payload gains template + course/star enums"
```

- [ ] **Step 5: Human audit (human-audit skill)**

Summarize what changed, point the user at the Segments tab, and wait for their live verdict before closing out. After their sign-off, merge per the repo rules (`--no-ff`, full suite on the merged result, delete the branch) and run the create-artifacts skill.

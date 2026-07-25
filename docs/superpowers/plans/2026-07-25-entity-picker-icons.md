# Entity Picker Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the native-`<select>` entity picker with a searchable modal that shows each course's portrait and each star's icon, so a user recognises what they're picking instead of reading it.

**Architecture:** A trigger button opens a dialog on the shared `Modal` shell containing a search box and a grouped, keyboard-driven list of icon rows. Icon resolution is one pure function in `ui/entities.js` reusing the practice banner's chain; the art manifest comes from a globbing server endpoint so dropping in new art needs no code change.

**Tech Stack:** Preact + htm ESM served as-is (no build step), Python 3.12 / FastAPI / pytest, node for pure-JS unit tests.

**Spec:** `docs/superpowers/specs/2026-07-25-entity-picker-icons-design.md`

## Global Constraints

- Branch off current `main`; run everything from the repo root (or the feature worktree root).
- `uv run pytest -q` must pass before every commit. Never `pip`. **Baseline: 1539 passed.**
- UI components import Preact through index.html's **importmap** under BARE specifiers (`import { h } from "preact"`, `"preact/hooks"`, `"htm"`, then `const html = htm.bind(h)`). There is no `vendor/preact.js`.
- **Node cannot execute a module that imports `preact`/`htm`.** Pure logic that needs a node test lives in an import-free module (`ui/group.js`, `ui/entities.js`). Syntax-check components with `node --input-type=module --check < file.js`; verify behaviour by rendering.
- Don't start `python -m sm64_events.main` — the user may be playing and it takes the recorder lock. UI verification uses a static harness on port **8137** (never 8064/8065/8066), deleted and killed in the same task.
- Run verification through the **Bash tool** — PowerShell mangles native exit codes.
- **HMC, SSL, DDD and SL have no course portrait and never will** — those courses aren't entered through a painting. Their fallback to star-1 art is the final answer; do not add a TODO or go looking for the files.
- No single-letter variables, including JS callbacks. Match each file's comment density.
- Commit messages explain WHY.

---

## File Structure

| File | Responsibility | Owner |
|---|---|---|
| `src/sm64_events/ui/assets/course_icons/` | the portraits, moved into the served + bundled tree | Task 1 |
| `src/sm64_events/server/api.py` | `GET /api/icons/courses` — globs the directory, returns stem → filename | Task 1 |
| `src/sm64_events/tracking/segments.py` | `vocab()["course_by_level"]` so JS can map a level to its course | Task 1 |
| `src/sm64_events/ui/entities.js` | `COURSE_ICON_PREFIXES`, `LEVEL_ICONS`, `optionIcon()` — the ONE icon chain | Task 2 |
| `src/sm64_events/ui/components/stagebanner.js` | imports those two registries from `entities.js` instead of defining them | Task 2 |
| `src/sm64_events/ui/components/entitymodal.js` (new) + `index.html` | `EntityPicker` trigger + dialog, and its CSS | Task 3 |
| `src/sm64_events/ui/store.js` | fetches the portrait manifest once, exposes `t.courseIcons` | Task 3 |
| `src/sm64_events/ui/components/segments.js` | clause params use `EntityPicker` | Task 4 |
| `src/sm64_events/ui/components/header.js` | target modal uses `EntityPicker` | Task 5 |
| `src/sm64_events/ui/components/routes.js` | step editor uses `EntityPicker` | Task 6 |
| `src/sm64_events/ui/components/picker.js` (delete) + `tests/test_ui_picker_parity.py` + `.claude/rules/ui.md` | remove the superseded control, retarget the gate | Task 7 |

## Waves

- **Wave 1 (parallel, disjoint):** Task 1 · Task 2 · Task 3
- **Wave 2 (parallel, disjoint):** Task 4 · Task 5 · Task 6
- **Wave 3:** Task 7

---

### Task 1: Serve the portraits, and tell JS which course a level belongs to

**Files:**
- Move: `assets/course_icons/*` → `src/sm64_events/ui/assets/course_icons/`
- Modify: `src/sm64_events/server/api.py` (beside `/icons`, ~line 579)
- Modify: `src/sm64_events/tracking/segments.py` (`vocab()`, the returned dict)
- Test: `tests/test_api.py`, `tests/test_segments.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `GET /api/icons/courses` → `{"courses": {"bob": "bob.webp", "rr": "rr.png", …}}` — stem → actual filename
  - `vocab()["course_by_level"]` → `{"9": 1, "24": 2, …}` (level id → course id, string keys for JSON)

- [ ] **Step 1: Move the assets**

```bash
mkdir -p src/sm64_events/ui/assets/course_icons
git mv assets/course_icons/* src/sm64_events/ui/assets/course_icons/ 2>/dev/null \
  || mv assets/course_icons/* src/sm64_events/ui/assets/course_icons/
ls src/sm64_events/ui/assets/course_icons/
```
(`git mv` fails if the files were never tracked — they are currently untracked, so the fallback `mv` is expected. `git add` them in Step 6.)

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_course_icons_endpoint_maps_stems_to_real_filenames(tmp_path):
    # The extensions are mixed (.webp and .png), so the client must never
    # guess: it asks for the directory listing, exactly as /api/icons does for
    # star_icons. Dropping new art in the folder then needs no code change.
    client, service, db = make_client(tmp_path)
    with client:
        courses = client.get("/api/icons/courses").json()["courses"]
        assert courses["bob"].startswith("bob.")
        assert courses["rr"].startswith("rr.")
        # every value is a real file in the bundled directory
        from pathlib import Path
        import sm64_events
        asset_dir = (Path(sm64_events.__file__).parent / "ui" / "assets"
                     / "course_icons")
        for stem, filename in courses.items():
            assert (asset_dir / filename).exists(), stem


def test_course_icons_omit_the_four_courses_the_game_has_no_painting_for(tmp_path):
    # HMC, SSL, DDD and SL are not entered through a painting, so no portrait
    # exists. The picker falls back to star-1 art for them; this asserts we
    # aren't silently shipping a wrong file under those names.
    client, service, db = make_client(tmp_path)
    with client:
        courses = client.get("/api/icons/courses").json()["courses"]
        for stem in ("hmc", "ssl", "ddd", "sl"):
            assert stem not in courses, stem
```

Append to `tests/test_segments.py`:

```python
def test_vocab_ships_course_by_level():
    # The JS icon chain maps a level to its course; the mapping is domain data
    # and stays server-side rather than being duplicated in the UI.
    shipped = vocab()["course_by_level"]
    assert shipped["9"] == 1        # BoB
    assert shipped["24"] == 2       # WF
    assert all(isinstance(key, str) for key in shipped)   # JSON object keys
```

Extend the `sm64_events.tracking.segments` import in that file with nothing new
(`vocab` is already imported).

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_api.py -q -k course_icons` and
`uv run pytest tests/test_segments.py -q -k course_by_level`
Expected: FAIL — 404 for the endpoint, `KeyError: 'course_by_level'`

- [ ] **Step 4: Implement**

In `src/sm64_events/server/api.py`, beside the existing `/icons` route:

```python
    @router.get("/icons/courses")
    async def course_icons():
        """Course portrait art: stem -> actual filename.

        The set is MIXED-extension (.webp and .png), so the client cannot
        build a URL from a stem alone — it asks for the listing, exactly as
        /api/icons does for star_icons. Consequence, and the reason for the
        endpoint: re-art or a higher-resolution rip appears by dropping the
        file in the folder, with no code change.

        Four main courses are absent on purpose — HMC, SSL, DDD and SL are not
        entered through a painting, so the game has no portrait for them. The
        UI falls back to their star-1 icon (ui/entities.js optionIcon).
        """
        if not _COURSE_ICON_DIR.is_dir():
            return {"courses": {}}
        return {"courses": {path.stem: path.name
                            for path in sorted(_COURSE_ICON_DIR.iterdir())
                            if path.is_file()}}
```

with the directory constant beside the existing `_ICON_DIR` (~line 95),
resolved the same way — **there is no `ui_dir()` helper in `core/paths.py`;
`api.py` resolves the bundled asset dirs relative to the package so they work
from source and frozen alike**:

```python
# Course portrait art (ui/assets/course_icons), resolved like _ICON_DIR above
# and globbed per call for the same reason: a dropped file shows up without a
# restart.
_COURSE_ICON_DIR = Path(__file__).resolve().parents[1] / "ui" / "assets" / "course_icons"
```

In `src/sm64_events/tracking/segments.py`, add to the dict `vocab()` returns,
after `"course_groups": course_groups(),`:

```python
        # Level -> course, so the UI's icon chain can find a level's art
        # without duplicating COURSE_BY_LEVEL in JS. String keys: JSON object
        # keys are strings, and the client indexes with String(level).
        "course_by_level": {str(level): course
                            for level, course in COURSE_BY_LEVEL.items()},
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_api.py tests/test_segments.py -q`
Expected: PASS

Run: `uv run pytest -q` (full suite; **baseline 1539**)
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/assets/course_icons src/sm64_events/server/api.py \
        src/sm64_events/tracking/segments.py tests/test_api.py tests/test_segments.py
git commit -F- <<'MSG'
feat(api): serve the course portraits, and map levels to courses

The portraits lived outside the served tree and untracked, so nothing could
render them and a build would not have bundled them. Moved under ui/assets/,
which tools/build_exe.py already ships whole.

The endpoint returns stem -> FILENAME rather than a list, because the set is
mixed .webp/.png and a client that guesses the extension breaks the moment
someone re-rips one. Globbing means new art needs no code change — the same
property /api/icons already gives star_icons.

Four courses are absent and always will be: HMC, SSL, DDD and SL are not
entered through a painting, so the game has no portrait to ship. The UI falls
back to their star-1 icon. Pinned by a test so nobody "fixes" it by dropping a
wrong file under those names.

course_by_level lets the UI find a level's art without a second copy of a
mapping that is domain data.
MSG
```

---

### Task 2: One icon chain, one home

**Files:**
- Modify: `src/sm64_events/ui/entities.js` (append)
- Modify: `src/sm64_events/ui/components/stagebanner.js` (import the two registries instead of defining them, ~lines 100-115)
- Test: `tests/test_ui_entities.py`

**Interfaces:**
- Consumes: Task 1's `/api/icons/courses` shape and `vocab().course_by_level` (as data passed in — this module fetches nothing).
- Produces:
  - `COURSE_ICON_PREFIXES` (moved from stagebanner.js)
  - `LEVEL_ICONS` (moved from stagebanner.js)
  - `optionIcon(kind, id, context) -> string` — an image URL, never null
  - `context` = `{ courseIcons, starIconsMode, iconOverrides, courseByLevel, segmentLevels }`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui_entities.py`:

```python
CONTEXT = """
const context = {
  courseIcons: { bob: "bob.webp", rr: "rr.png" },   // hmc/ssl/ddd/sl absent
  starIconsMode: "course",
  iconOverrides: { "segment:7": "bitfs" },
  courseByLevel: { "9": 1, "22": 7, "17": 16 },
  segmentLevels: { "7": [19], "9": [6] },
};
"""


def test_course_icon_prefers_the_portrait():
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("course", "1", context)));')
    assert src == "/ui/assets/course_icons/bob.webp"


def test_a_course_with_no_painting_falls_back_to_its_star_one_icon():
    # LLL is course 7; the game has no LLL portrait, so this is the FINAL
    # answer for it, not a placeholder awaiting art.
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("course", "7", context)));')
    assert src == "/ui/assets/star_icons/lll1.png"


def test_star_icon_follows_the_user_preference():
    per_star = run_node("optionIcon", CONTEXT
                        + 'console.log(JSON.stringify(optionIcon("star", "1:2", context)));')
    assert per_star == "/ui/assets/star_icons/bob3.png"
    classic = run_node("optionIcon", CONTEXT + """
const classicContext = { ...context, starIconsMode: "classic" };
console.log(JSON.stringify(optionIcon("star", "1:2", classicContext)));
""")
    assert classic.startswith("/ui/assets/star_"), classic
    assert "star_icons" not in classic


def test_level_icon_routes_through_its_course():
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("level", "9", context)));')
    assert src == "/ui/assets/course_icons/bob.webp"


def test_bowser_levels_use_their_own_art():
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("level", "17", context)));')
    assert src == "/ui/assets/star_icons/bitdw.png"


def test_segment_icon_uses_the_override_the_banner_uses():
    src = run_node("optionIcon", CONTEXT
                   + 'console.log(JSON.stringify(optionIcon("segment", "7", context)));')
    assert src == "/ui/assets/star_icons/bitfs.png"


def test_every_kind_returns_art_never_null():
    # A picker row with no icon would collapse its layout; the chain always
    # ends at the generic star.
    for call in ('optionIcon("course", "99", context)',
                 'optionIcon("level", "26", context)',
                 'optionIcon("segment", "9", context)',
                 'optionIcon("nonsense", "x", context)'):
        src = run_node("optionIcon", CONTEXT
                       + f'console.log(JSON.stringify({call}));')
        assert src.startswith("/ui/assets/"), (call, src)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_ui_entities.py -q`
Expected: FAIL — `SyntaxError: The requested module does not provide an export named 'optionIcon'`

- [ ] **Step 3: Implement in `entities.js`**

Append:

```js
// --- Entity art -----------------------------------------------------------
// ONE chain, shared by the practice banner and the picker, so the same star
// never wears different art in two places. It lives here because this module
// imports nothing and is therefore node-testable; components/stagebanner.js
// imports these two registries rather than keeping its own copies.

// ui/assets/star_icons/{prefix}{slot+1}.png, one per main-course star
// INCLUDING the 100-coin 7th slot. Index = course_id - 1 (catalog order,
// pinned against the assets by tests/test_star_icons.py).
export const COURSE_ICON_PREFIXES = ["bob", "wf", "jrb", "ccm", "bbh", "hmc",
                                     "lll", "ssl", "ddd", "sl", "wdw", "ttm",
                                     "thi", "ttc", "rr"];

// The icon set has real art for the Bowser stages, keyed by both the course
// level (pipe-entry segments) and its fight arena.
export const LEVEL_ICONS = { 17: "bitdw", 19: "bitfs", 21: "bits",
                             30: "bitdw", 33: "bitfs", 34: "bits" };

const GENERIC_STAR_SLOTS = 6;   // ui/assets/star_1.png … star_6.png
const genericStar = (slot = 0) =>
  `/ui/assets/star_${Math.min(slot + 1, GENERIC_STAR_SLOTS)}.png`;
const starIconSrc = (stem) => `/ui/assets/star_icons/${stem}.png`;

/**
 * Art for one picker row. ALWAYS returns a URL — a row with no icon would
 * collapse its own layout, so every branch ends at the generic star.
 *
 * kind    "course" | "star" | "level" | "segment"
 * id      the option id (a star's is composite, "8:2")
 * context { courseIcons     stem -> filename, from GET /api/icons/courses
 *           starIconsMode   "course" | "classic", the user's setting
 *           iconOverrides   view.icon_overrides, per-entity user picks
 *           courseByLevel   vocab.course_by_level
 *           segmentLevels   segment id -> its start levels }
 *
 * Four main courses (HMC, SSL, DDD, SL) have no portrait because the game has
 * no painting for them; they resolve to their star-1 icon, which is the final
 * answer, not a placeholder.
 */
export function optionIcon(kind, id, context = {}) {
  const { courseIcons = {}, starIconsMode = "course", iconOverrides = {},
          courseByLevel = {}, segmentLevels = {} } = context;
  const prefixFor = (course) => COURSE_ICON_PREFIXES[Number(course) - 1] || null;
  const courseArt = (course) => {
    const prefix = prefixFor(course);
    if (!prefix) return genericStar();
    if (courseIcons[prefix]) return `/ui/assets/course_icons/${courseIcons[prefix]}`;
    return starIconSrc(`${prefix}1`);     // no painting in the game — final
  };

  if (kind === "course") return courseArt(id);
  if (kind === "star") {
    const { course, star } = parseStarId(id);
    if (starIconsMode !== "course") return genericStar(star);
    const prefix = prefixFor(course);
    return prefix ? starIconSrc(`${prefix}${star + 1}`) : genericStar(star);
  }
  if (kind === "level") {
    const level = Number(id);
    if (LEVEL_ICONS[level]) return starIconSrc(LEVEL_ICONS[level]);
    const course = courseByLevel[String(level)];
    return course ? courseArt(course) : genericStar();
  }
  if (kind === "segment") {
    const override = iconOverrides[`segment:${id}`];
    if (override) return starIconSrc(override);
    const stem = (segmentLevels[String(id)] || [])
      .map((level) => LEVEL_ICONS[level]).find(Boolean);
    return stem ? starIconSrc(stem) : genericStar();
  }
  return genericStar();
}
```

Note `starIconSrc` deliberately does NOT handle the `user:` upload prefix —
that rule lives in `iconpicker.js::iconSrcFromStem`, and the picker's segment
branch passes stems that came from the same overrides map. If an uploaded icon
must render in the picker, route it through that function rather than
duplicating the rule here.

- [ ] **Step 4: Point stagebanner.js at the shared registries**

In `src/sm64_events/ui/components/stagebanner.js`, delete the local
`COURSE_ICON_PREFIXES` and `LEVEL_ICONS` definitions (~lines 100-115) and
import them instead, keeping every existing comment about what they mean:

```js
import { COURSE_ICON_PREFIXES, LEVEL_ICONS } from "../entities.js";
```

Nothing else in that file changes — it keeps its own `resolveIcon`, which
handles the `user:` prefix and the generic-art detection the banner needs.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q` (**baseline 1539**)
Expected: PASS

Run: `node --input-type=module --check < src/sm64_events/ui/entities.js` and the
same for `stagebanner.js`
Expected: exit 0 each

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/entities.js src/sm64_events/ui/components/stagebanner.js tests/test_ui_entities.py
git commit -F- <<'MSG'
feat(ui): one entity-art chain, shared by the banner and the picker

optionIcon resolves a picker row's art the same way the practice banner
resolves a cell's — including honouring the per-star-vs-classic preference — so
the same star cannot wear different art in two places. It lives in entities.js
because that module imports nothing and can be unit-tested through node, and
stagebanner.js now imports the two registries instead of owning copies.

Every branch ends at the generic star: a row with no icon collapses its own
layout, so "no art" is never a possible answer.

The four courses without a portrait resolve to their star-1 icon. That is the
FINAL answer for them — the game has no painting for HMC, SSL, DDD or SL — not
a placeholder waiting for assets.
MSG
```

---

### Task 3: The picker itself

**Files:**
- Create: `src/sm64_events/ui/components/entitymodal.js`
- Modify: `src/sm64_events/ui/index.html` (CSS only)
- Modify: `src/sm64_events/ui/store.js` (fetch the portrait manifest once)
- Test: `tests/test_ui_entitymodal.py` (new — source contracts; behaviour is the render check)

**Interfaces:**
- Consumes: `visibleGroups` (already in `entities.js`); the group shape `{key, label, options:[{id, name}]}`.
- Produces:
  - `EntityPicker({ groups, value, onChange, allow, title, iconFor, disabled, placeholder })`
  - `iconFor(id) -> string` — the caller supplies it (built from Task 2's `optionIcon`), so this component stays domain-free
  - `onChange(id | null)`
  - **`t.courseIcons`** — the `{stem: filename}` manifest from `GET /api/icons/courses`, fetched once by the store. Tasks 4, 5 and 6 all read it; without it every course row silently falls back to star art, which looks like the fallback working rather than a missing fetch.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_entitymodal.py`:

```python
"""Source contracts for the modal entity picker.

The real verification is the render check in this task's Step 5 — a custom
control's keyboard path cannot be proven by reading. These pin the pieces that
a refactor could silently drop.
"""
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
MODAL = (UI / "components" / "entitymodal.js").read_text(encoding="utf-8")
INDEX = (UI / "index.html").read_text(encoding="utf-8")


def test_reuses_the_shared_filter_rather_than_reimplementing_it():
    # The keep-the-current-value-listed invariant has its own tests against
    # entities.js; the picker must not grow a second copy of that logic.
    assert "visibleGroups" in MODAL
    assert "from \"../entities.js\"" in MODAL


def test_keyboard_contract_is_implemented():
    # What native <select> gave for free and a custom control must earn back.
    for key in ("ArrowDown", "ArrowUp", "Enter", "Escape"):
        assert key in MODAL, key


def test_rows_are_listbox_options_for_screen_readers():
    assert 'role="listbox"' in MODAL
    assert 'role="option"' in MODAL
    assert "aria-activedescendant" in MODAL


def test_the_component_owns_no_domain_vocabulary():
    # Icons come from the caller via iconFor(); the picker must not learn what
    # a course or a star is. Comments stripped first — a guard that a comment
    # can satisfy is not a guard (learned 2026-07-25).
    import re
    source = re.sub(r"/\*.*?\*/", "", MODAL, flags=re.S)
    source = re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)
    for domain_word in ("course", "star", "vocab", "catalog", "segment"):
        assert domain_word not in source.lower(), domain_word


def test_row_art_has_a_fixed_box_so_a_missing_image_cannot_reflow_the_list():
    assert ".entity-row-icon" in INDEX
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_ui_entitymodal.py -q`
Expected: FAIL — `FileNotFoundError: .../entitymodal.js`

- [ ] **Step 3: Implement the component**

Create `src/sm64_events/ui/components/entitymodal.js`:

```js
import { h } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";
import { visibleGroups } from "../entities.js";

const html = htm.bind(h);

// THE entity picker: a trigger button that opens a searchable, grouped,
// keyboard-driven list in a dialog. It replaced a native <select> because
// <option> cannot contain an image and these rows carry art (spec
// 2026-07-25-entity-picker-icons).
//
// It knows nothing about what it is picking. Callers pass groups (built by
// ui/entities.js), their own filter as `allow`, and an `iconFor(id)` that
// resolves a row's art — so the domain stays outside this file, exactly as it
// did for the select this replaces.
//
// A DIALOG, not a popup anchored to the trigger: the workshop panes scroll
// internally under a measured height cap (ui/viewport.js), and an anchored
// popup inside a clipped scrolling pane is where custom dropdowns break.
//
// Keyboard is what native gave for free and this has to earn back: type to
// filter, Up/Down across group boundaries, Enter to pick, Escape to close.
// The Modal shell already traps focus and restores it to the trigger on close.

const matches = (text, needle) =>
  text.toLowerCase().includes(needle.trim().toLowerCase());

function PickerDialog({ groups, value, allow, title, iconFor, onPick, onClose }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef(null);

  // Filtered groups, and the flat row order the arrow keys walk. Derived
  // during render, never in an effect — an effect would paint the unfiltered
  // list first and then correct it.
  const [shownGroups, flatRows] = useMemo(() => {
    const filtered = visibleGroups(groups, allow, value)
      .map((group) => ({ ...group,
        options: group.options.filter((option) => matches(option.name, query)) }))
      .filter((group) => group.options.length > 0);
    return [filtered, filtered.flatMap((group) => group.options)];
  }, [groups, allow, value, query]);

  useEffect(() => { setActiveIndex(0); }, [query]);

  const move = (delta) => setActiveIndex((current) => {
    if (flatRows.length === 0) return 0;
    const next = (current + delta + flatRows.length) % flatRows.length;
    const node = listRef.current
      && listRef.current.querySelector(`[data-row="${next}"]`);
    if (node && node.scrollIntoView) node.scrollIntoView({ block: "nearest" });
    return next;
  });

  const onKeyDown = (keyEvent) => {
    if (keyEvent.key === "ArrowDown") { keyEvent.preventDefault(); move(1); }
    else if (keyEvent.key === "ArrowUp") { keyEvent.preventDefault(); move(-1); }
    else if (keyEvent.key === "Enter") {
      keyEvent.preventDefault();
      const row = flatRows[activeIndex];
      if (row) onPick(row.id);
    } else if (keyEvent.key === "Escape") { onClose(); }
  };

  let rowIndex = -1;
  return html`<${Modal} title=${title} icon="target" onClose=${onClose}>
    <input class="entity-search" type="search" autofocus value=${query}
      placeholder="Type to filter…" aria-label="Filter"
      oninput=${(inputEvent) => setQuery(inputEvent.target.value)}
      onkeydown=${onKeyDown} />
    <div class="entity-list" role="listbox" ref=${listRef}
        aria-activedescendant=${`entity-row-${activeIndex}`}>
      ${shownGroups.length === 0
        ? html`<p class="meta">Nothing matches “${query}”.</p>`
        : shownGroups.map((group) => html`<div class="entity-group"
            key=${group.key}>
          <div class="entity-group-head">
            ${group.icon
              ? html`<img class="entity-row-icon" src=${group.icon} alt="" />`
              : null}
            <b>${group.label}</b>
          </div>
          ${group.options.map((option) => {
            rowIndex += 1;
            const index = rowIndex;
            return html`<button type="button" key=${option.id}
                id=${`entity-row-${index}`} data-row=${index}
                role="option" aria-selected=${option.id === value}
                class=${`entity-row ${index === activeIndex ? "active" : ""} `
                       + `${option.id === value ? "chosen" : ""}`}
                onmousemove=${() => setActiveIndex(index)}
                onclick=${() => onPick(option.id)}>
              <img class="entity-row-icon" src=${iconFor(option.id)} alt=""
                loading="lazy" />
              <span>${option.name}</span>
            </button>`;
          })}
        </div>`)}
    </div>
  <//>`;
}

/**
 * groups      [{ key, label, icon?, options: [{ id, name }] }]
 * value       current id (string) or null
 * onChange    (id | null) => void
 * allow       optional (id) => boolean — the CALLER's domain filter
 * iconFor     (id) => image URL for a row
 * title       dialog heading, e.g. "Choose a star"
 * placeholder trigger label when nothing is chosen
 */
export function EntityPicker({ groups, value, onChange, allow, iconFor,
                              title = "Choose", placeholder = "— pick —",
                              disabled = false }) {
  const [open, setOpen] = useState(false);
  const current = visibleGroups(groups, allow, value)
    .flatMap((group) => group.options)
    .find((option) => option.id === value) || null;
  return html`<${h.Fragment}>
    <button type="button" class="entity-trigger" disabled=${disabled}
        aria-haspopup="dialog" onclick=${() => setOpen(true)}>
      ${current
        ? html`<img class="entity-row-icon" src=${iconFor(current.id)} alt="" />`
        : null}
      <span class="entity-trigger-label">${current ? current.name : placeholder}</span>
      <${Icon} name="chevron" size=${15} />
    </button>
    ${open ? html`<${PickerDialog} groups=${groups} value=${value} allow=${allow}
      title=${title} iconFor=${iconFor}
      onPick=${(id) => { setOpen(false); onChange(id); }}
      onClose=${() => setOpen(false)} />` : null}
  <//>`;
}
```

- [ ] **Step 4: Expose the portrait manifest on the store**

Nothing else fetches it, and all three call sites in wave 2 read `t.courseIcons`.
In `src/sm64_events/ui/store.js`, beside the other display state:

```js
  // Course portrait manifest (stem -> filename) from GET /api/icons/courses.
  // Fetched ONCE: the set only changes with the install. Empty object until it
  // lands, which the icon chain treats as "no portrait" and falls back to star
  // art — so a slow fetch degrades to the same art the four painting-less
  // courses use, never to a broken image.
  const [courseIcons, setCourseIcons] = useState({});
  useEffect(() => {
    getJSON("/api/icons/courses")
      .then((payload) => setCourseIcons(payload.courses || {}))
      .catch(() => {});      // no portraits is a survivable state
  }, []);
```

and add `courseIcons` to the object the hook returns (~line 254, beside
`starIcons`). Use whatever the file already imports for fetching (`getJSON`
from `./api.js`).

- [ ] **Step 5: Add the CSS**

In `src/sm64_events/ui/index.html`, after the `.builder-origin` rules (~line 137):

```css
  /* Entity picker (spec 2026-07-25-entity-picker-icons). The trigger reads
     like the select it replaced; the dialog rows carry art. */
  .entity-trigger {
    display: inline-flex; align-items: center; gap: .45rem;
    min-height: 34px; min-width: 0; max-width: 100%;
    padding: .3rem .5rem; text-align: left;
  }
  .entity-trigger-label {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  /* Fixed box: art loads asynchronously and some rows fall back to different
     art entirely, so the row must not reflow when an image resolves. */
  .entity-row-icon {
    width: 22px; height: 22px; flex: 0 0 22px;
    object-fit: contain; border-radius: 4px;
  }
  .entity-search { width: 100%; margin-bottom: .6rem; }
  .entity-list {
    max-height: min(60vh, 460px); overflow-y: auto; overflow-x: hidden;
    display: grid; align-content: start; gap: .15rem;
  }
  .entity-group + .entity-group { margin-top: .5rem; }
  .entity-group-head {
    display: flex; align-items: center; gap: .4rem;
    padding: .25rem .3rem; color: var(--muted);
    font-size: .72rem; letter-spacing: .04em; text-transform: uppercase;
  }
  .entity-row {
    width: 100%; display: flex; align-items: center; gap: .5rem;
    padding: .32rem .45rem; margin-left: .5rem;
    background: transparent; border-color: transparent; text-align: left;
  }
  .entity-row.active { background: rgba(61,101,143,.28); border-color: #6287aa; }
  .entity-row.chosen { color: var(--gold-soft); }
```

- [ ] **Step 6: Verify by rendering (mandatory)**

Serve `python -m http.server 8137 --directory src/sm64_events/ui` (port 8137
only) with a throwaway harness mounting `EntityPicker` with two groups, a
stubbed `iconFor` returning a real asset URL, and `allow` rejecting one option
whose id is the current `value`. Drive it with `chrome --headless=new` or the
chrome-devtools MCP, and confirm:
1. the trigger shows the current option's icon and name;
2. clicking opens the dialog; rows show art;
3. typing filters rows AND removes a group whose rows all filter out;
4. ArrowDown/ArrowUp move the highlight **across a group boundary**;
5. Enter picks the active row and closes; the value updates;
6. Escape closes without changing the value;
7. the filter-rejected current value is still listed;
8. no JS errors in the console.
**Delete the harness and kill port 8137 in this same task.**

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/ui/components/entitymodal.js src/sm64_events/ui/index.html         src/sm64_events/ui/store.js tests/test_ui_entitymodal.py
git commit -F- <<'MSG'
feat(ui): a searchable entity picker that shows what you are choosing

<option> cannot hold an image, so recognising a course by its painting means
replacing the native control. A dialog rather than a popup anchored to the
trigger: the workshop panes scroll internally under a measured height cap, and
an anchored popup inside a clipped scrolling pane is where custom dropdowns
break.

It stays domain-free the way the select did — groups and an iconFor(id) come
from the caller, and it reuses entities.js's visibleGroups so the
keep-the-current-value-listed invariant keeps its existing tests rather than
being reimplemented here.

Filtering is derived during render, not in an effect: an effect would paint the
unfiltered list and then correct it, which is the render-glitch class this
codebase already has a diagnosis for.

The row art box is fixed-size because art loads asynchronously and some rows
fall back to different art entirely — a row that reflows when an image resolves
makes the whole list jump.
MSG
```

---

### Task 4: The segment builder's clause params

**Files:**
- Modify: `src/sm64_events/ui/components/segments.js` (`ParamInput`)
- Test: `tests/test_segments_editor_ui.py`

**Interfaces:**
- Consumes: `EntityPicker` (Task 3); `optionIcon`, `levelOptions`, `courseOptions`, `starOptionsFromVocab`, `parseStarId`, `starId` (Tasks 2 and existing).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_segments_editor_ui.py`:

```python
def test_clause_params_use_the_modal_picker():
    assert "EntityPicker" in SEGMENTS_JS_SOURCE
    assert "GroupedPicker" not in SEGMENTS_JS_SOURCE   # the select is gone


def test_the_topology_filter_still_lives_here():
    # Unchanged by the control swap: the picker never learns about world edges.
    assert "allowedIds" in SEGMENTS_JS_SOURCE
    assert "allow=${" in SEGMENTS_JS_SOURCE


def test_icons_are_resolved_by_the_call_site():
    # iconFor is the caller's, so the picker stays domain-free.
    assert "iconFor=${" in SEGMENTS_JS_SOURCE
    assert "optionIcon" in SEGMENTS_JS_SOURCE
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_segments_editor_ui.py -q`
Expected: FAIL — `assert "EntityPicker" in SEGMENTS_JS_SOURCE`

- [ ] **Step 3: Implement**

Swap the import and each of the three branches. Imports:

```js
import { EntityPicker } from "./entitymodal.js";
import { courseOptions, levelOptions, optionIcon, parseStarId, starId,
         starOptionsFromVocab } from "../entities.js";
```

`ParamInput` needs the icon context. It already receives `vocab`; add a
`context` prop threaded from `Builder` (which has `t`):

```js
  // Icon context assembled HERE, not inside the picker: the picker resolves
  // no domain art of its own.
  const iconContext = {
    courseIcons: (t && t.courseIcons) || {},
    starIconsMode: (t && t.starIcons) || "course",
    courseByLevel: vocab.course_by_level || {},
  };
```

Level branch:

```js
  if (schema.kind === "level") {
    const groups = levelOptions(vocab).map((group) => ({
      ...group,
      options: group.options.filter((option) =>
        !schema.enum || schema.enum.includes(Number(option.id))),
    })).filter((group) => group.options.length > 0);
    return html`<${EntityPicker} groups=${groups} allow=${permittedId}
      value=${value == null ? null : String(value)}
      title="Choose a level"
      placeholder=${schema.required ? "— pick level —" : "(any level)"}
      iconFor=${(id) => optionIcon("level", id, iconContext)}
      onChange=${(id) => onChange(id == null ? null : Number(id))} />`;
  }
```

Course branch:

```js
  if (schema.kind === "course")
    return html`<${EntityPicker} groups=${courseOptions(vocab)}
      value=${value == null ? null : String(value)}
      title="Choose a course"
      placeholder=${schema.required ? "— pick course —" : "(any course)"}
      iconFor=${(id) => optionIcon("course", id, iconContext)}
      onChange=${(id) => onChange(id == null ? null : Number(id))} />`;
```

Star branch — still edits ONE param, so it narrows to the sibling course and
unpacks the composite id, exactly as before:

```js
  if (schema.kind === "star") {
    const groups = starOptionsFromVocab(vocab)
      .filter((group) => group.key === `course-${clause.course}`);
    return html`<${EntityPicker} groups=${groups}
      disabled=${clause.course == null}
      value=${value == null ? null : starId(clause.course, value)}
      title="Choose a star"
      placeholder=${schema.required ? "— pick star —" : "(any star)"}
      iconFor=${(id) => optionIcon("star", id, iconContext)}
      onChange=${(id) => onChange(id == null ? null : parseStarId(id).star)} />`;
  }
```

Leave `subarea`, `seconds` and the numeric fallback exactly as they are — the
3-item subarea list has nothing to group or illustrate, and both numeric inputs
must keep `oninput` (a poll re-render wipes uncommitted `onchange` values).

`ParamInput` is called from `ClauseRow`; thread `t` down from `Builder` →
`section(...)` → `ClauseRow` → `ParamInput` so `iconContext` can be built.
`Builder` already receives `t`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q` (**baseline 1539 + earlier waves' tests**)
Expected: PASS

Run: `node --input-type=module --check < src/sm64_events/ui/components/segments.js`
Expected: exit 0

- [ ] **Step 5: Verify by rendering (mandatory)**

Harness `ParamInput` per Task 3's technique (port 8137, delete + kill in-task).
Confirm: the level trigger shows the level's art; opening it lists regions with
portraits; **with `clause.from = 16` it narrows to Castle Inside + VCUtM and
still shows both group headings**; the star control is disabled until a course
is picked; picking a star changes only the star param. Report each.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/components/segments.js tests/test_segments_editor_ui.py
git commit -F- <<'MSG'
refactor(ui): segment builder params pick through the icon modal

Same filtering, same one-param-per-control discipline, same composite-id
unpacking — only the control changed. The topology filter stays here as
`allow`, and the icon context is assembled here too, so the picker resolves no
domain art of its own.
MSG
```

---

### Task 5: The practice-target modal

**Files:**
- Modify: `src/sm64_events/ui/components/header.js`
- Test: `tests/test_header_ui.py`

**Interfaces:**
- Consumes: `EntityPicker` (Task 3); `optionIcon`, `starOptionsFromCatalog`, `parseStarId`, `starId` (Task 2 + existing).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_header_ui.py`:

```python
def test_target_picker_is_the_icon_modal():
    assert "EntityPicker" in HEADER_JS
    assert "GroupedPicker" not in HEADER_JS
    assert "optionIcon" in HEADER_JS


def test_course_portraits_ride_the_group_heading():
    # The course is the group; its portrait belongs on the heading, not
    # repeated on all seven star rows (spec decision 3).
    assert "icon:" in HEADER_JS or "group.icon" in HEADER_JS
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_header_ui.py -q`
Expected: FAIL — `assert "EntityPicker" in HEADER_JS`

- [ ] **Step 3: Implement**

Imports:

```js
import { EntityPicker } from "./entitymodal.js";
import { optionIcon, parseStarId, starId, starOptionsFromCatalog } from "../entities.js";
```

Replace the star `GroupedPicker` with the modal, and attach the course portrait
to each group heading (the groups are courses — spec decision 3):

```js
  const iconContext = {
    courseIcons: t.courseIcons || {},
    starIconsMode: t.starIcons || "course",
  };
  // The group IS a course, so its heading carries the portrait — once per
  // course rather than repeated on all seven star rows.
  const starGroups = starOptionsFromCatalog(v.catalog).map((group) => ({
    ...group,
    icon: optionIcon("course", String(parseStarId(group.options[0].id).course),
                     iconContext),
  }));
```

```js
      <label>Star<${EntityPicker} groups=${starGroups}
        value=${starId(Number(course), Number(star))}
        title="Choose a star"
        iconFor=${(id) => optionIcon("star", id, iconContext)}
        onChange=${(id) => {
          const picked = parseStarId(id);
          pickStar(picked.course, picked.star);
        }} /></label>
```

`apply()` is unchanged — it still posts numeric `course_id` / `star_id` from the
state `pickStar` sets, and the Strategy select stays exactly as it is.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`; `node --input-type=module --check < src/sm64_events/ui/components/header.js`
Expected: PASS; exit 0

- [ ] **Step 5: Verify by rendering (mandatory)**

Harness the target editor (port 8137, delete + kill in-task) with a stubbed
`v.catalog` carrying `course_groups` and a stubbed `t.courseIcons`. Confirm and
report: the trigger shows the current star's art and name; the dialog groups by
course with the **portrait on the heading**; a course with no painting (HMC)
shows its star-1 art rather than a broken image; typing "pyramid" filters to
SSL's star; Enter picks it; `Set target` still POSTs numeric ids.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/components/header.js tests/test_header_ui.py
git commit -F- <<'MSG'
feat(ui): the practice target shows the course you are picking

The picker a user touches most, and the last one identifying a course by name
alone. The course is the group, so its portrait sits on the heading once
instead of repeating on all seven star rows, and star rows follow the existing
per-star-vs-classic preference rather than inventing a second setting.

The wire format is untouched: the composite id still unpacks into the same
numeric course_id and star_id through the same pickStar that re-resolves the
strategy list.
MSG
```

---

### Task 6: The route step editor

**Files:**
- Modify: `src/sm64_events/ui/components/routes.js` (`ItemPicker`)
- Test: `tests/test_ui_grouplist.py`

**Interfaces:**
- Consumes: `EntityPicker` (Task 3); `optionIcon`, `starOptionsFromCatalog`, `segmentOptions`, `parseStarId` (Task 2 + existing).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_grouplist.py`:

```python
def test_route_item_picker_uses_the_icon_modal():
    assert "EntityPicker" in ROUTES
    assert "GroupedPicker" not in ROUTES
    assert "optionIcon" in ROUTES


def test_segment_rows_carry_the_art_the_banner_uses():
    # segmentLevels feeds optionIcon's segment branch, which reuses the
    # banner's override -> level-fallback chain.
    assert "segmentLevels" in ROUTES
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_ui_grouplist.py -q`
Expected: FAIL — `assert "EntityPicker" in ROUTES`

- [ ] **Step 3: Implement**

Imports:

```js
import { EntityPicker } from "./entitymodal.js";
import { optionIcon, parseStarId, segmentOptions,
         starOptionsFromCatalog } from "../entities.js";
```

In `ItemPicker` (which already takes `catalog`, `segs`, `vocab`), build the icon
context — segments need their start levels, which `segs` carries:

```js
  const iconContext = {
    courseIcons: (t && t.courseIcons) || {},
    starIconsMode: (t && t.starIcons) || "course",
    iconOverrides: ((t && t.view) || {}).icon_overrides || {},
    segmentLevels: Object.fromEntries(
      (segs || []).map((segment) => [String(segment.id),
                                     segment.start_levels || []])),
  };
```

`ItemPicker` therefore needs `t`; thread it from `Routes` (which has it) through
`StepRow` exactly as `vocab` already is — `StepRow`'s props are
`{ step, view, idx, total, catalog, segs, vocab, onChange, onMove, onRemove, weakest }`.

Star branch:

```js
      ? html`<${EntityPicker} groups=${starGroups} value=${star}
          title="Choose a star"
          iconFor=${(id) => optionIcon("star", id, iconContext)}
          onChange=${(id) => setStar(id)} />`
```

Segment branch:

```js
        : html`<${EntityPicker} groups=${segGroups} value=${segId}
            title="Choose a segment"
            iconFor=${(id) => optionIcon("segment", id, iconContext)}
            onChange=${(id) => setSegId(id)} />`
```

`pick()` is unchanged — it still guards against a null selection and converts
ids to numbers at that one boundary.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q`; `node --input-type=module --check < src/sm64_events/ui/components/routes.js`
Expected: PASS; exit 0

- [ ] **Step 5: Verify by rendering (mandatory)**

Harness `ItemPicker` (port 8137, delete + kill in-task). Confirm and report:
star mode shows one grouped picker with course portraits on headings; segment
mode shows segments grouped by castle region, each row carrying the same art
the practice banner gives it (a BitFS segment shows the BitFS icon); `Add`
still reports numeric `course`/`star`/`segment_id`; rendering with `vocab`
undefined does not crash.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/components/routes.js tests/test_ui_grouplist.py
git commit -F- <<'MSG'
refactor(ui): route steps pick through the icon modal

Segment rows carry the same art the practice banner gives that segment —
override first, then its start level — so choosing a step off this list looks
like choosing it off the banner. The star groups carry course portraits on
their headings, matching the target picker.

pick() is untouched: ids stay strings inside the picker and convert once at
that boundary, so the step candidates on the wire stay numeric.
MSG
```

---

### Task 7: Remove the superseded control and retarget the gate

**Files:**
- Delete: `src/sm64_events/ui/components/picker.js`
- Delete: `tests/test_ui_picker.py` (its `visibleGroups` cases live in `tests/test_ui_entities.py`; **move any case not already duplicated there before deleting**)
- Modify: `tests/test_ui_picker_parity.py`, `.claude/rules/ui.md`

**Interfaces:** none.

- [ ] **Step 1: Check what would be lost**

Run: `uv run pytest tests/test_ui_picker.py -q --collect-only`
Compare its four cases against `tests/test_ui_entities.py`. `visibleGroups`
lives in `entities.js`, so its tests belong in `test_ui_entities.py` — **move
any case that is not already there**, then delete the file. Report which cases
you moved.

- [ ] **Step 2: Retarget the parity gate**

In `tests/test_ui_picker_parity.py`, change the shared-component assertion from
`GroupedPicker` to `EntityPicker`, and add `components/entitymodal.js` to the
allowlist of files permitted to render `<option>`-shaped markup **only if it
actually needs to be there** — it renders `<button role="option">`, not
`<option>`, so it probably does not. Keep the shape scan and the
domain-vocabulary guard exactly as they are.

Then **prove the gate still bites**, the same way it was proved before:

```bash
cat > src/sm64_events/ui/components/_probe.js <<'EOF'
export function Probe({ catalog }) {
  return catalog.courses.map((course) => `<option value=${course.id}>${course.name}</option>`);
}
EOF
uv run pytest tests/test_ui_picker_parity.py -q   # MUST fail
rm src/sm64_events/ui/components/_probe.js
uv run pytest tests/test_ui_picker_parity.py -q   # green again
```
Report both results.

- [ ] **Step 3: Delete the superseded component**

```bash
git rm src/sm64_events/ui/components/picker.js
grep -rn "GroupedPicker\|picker.js" src/ tests/ .claude/rules/ | grep -v test_ui_picker_parity
```
Expected: no live references. Fix any the grep finds.

- [ ] **Step 4: Update the change map**

In `.claude/rules/ui.md`, rewrite the "Entity pickers" row to describe the modal:
the trigger + dialog, `iconFor` supplied by the caller, the icon chain shared
with the banner (`optionIcon` in `entities.js`, with `COURSE_ICON_PREFIXES` and
`LEVEL_ICONS` now living there and `stagebanner.js` importing them), the
keyboard contract, `GET /api/icons/courses` globbing so new art needs no code
change, and that HMC/SSL/DDD/SL have no portrait **because the game has none**.
Keep the existing note that filtering lives at the call site.

- [ ] **Step 5: Full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A src/sm64_events/ui/components tests .claude/rules/ui.md
git commit -F- <<'MSG'
refactor(ui): delete the native picker the modal replaced

GroupedPicker shipped this morning and is superseded by the icon modal. It is
removed rather than left beside it: two controls for one job is the duplication
this whole line of work exists to end.

visibleGroups and its tests survive in entities.js — the invariant outlived the
control, which is why it lived outside the component.

The parity gate now requires EntityPicker, and I re-probed it: a hand-rolled
<option> list in a new file still turns it red.
MSG
```

---

## Final gate

- [ ] `uv run pytest -q` green
- [ ] Whole-branch review (`superpowers:requesting-code-review`, Opus 5) — three call sites changed in parallel plus a control swap
- [ ] **Human audit** — the keyboard path especially. A custom control's keyboard regressions are invisible to mouse users, and the star list is ~120 rows: open the target picker, type to filter, arrow through a group boundary, Enter, Escape.

## Self-review notes

- **Spec coverage:** §1 component → Task 3; §2 icon chain → Task 2 (+ Task 1 for the manifest and `course_by_level`); §3 keyboard → Task 3's implementation and its render check; §4 assets/serving → Task 1; §5 what this replaces → Task 7; §6 testing → tests in every task; §7 risks → lazy loading in Task 3's CSS/markup, the keyboard render check, and the single-chain reuse in Task 2.
- **Type consistency:** group shape `{key, label, icon?, options:[{id, name}]}` is produced by the `entities.js` builders and consumed by `EntityPicker`; ids are **strings** throughout, converted with `Number(...)`/`parseStarId(...)` at each call site's boundary; `optionIcon(kind, id, context)` takes the same `context` shape in all four call sites; `iconFor(id)` is always `(id) => optionIcon(<kind>, id, iconContext)`.
- **Two assumptions I checked and had to fix before anyone built on them:** there is no `ui_dir()` in `core/paths.py` — `api.py` resolves bundled asset dirs relative to the package (`Path(__file__).resolve().parents[1] / "ui" / ...`), so Task 1 follows `_ICON_DIR`'s existing pattern. And nothing fetched the portrait manifest: three tasks read `t.courseIcons` that no task created, which would have shipped every course row silently falling back to star art — looking like the fallback working rather than a missing fetch. Task 3 now owns that store change.
- **Verified while planning:** `Modal` already restores focus to the previously-focused element on unmount (`modal.js`), so the spec's "focus returns to the trigger" needs no new code — Task 3 relies on it rather than reimplementing it. `stagebanner.js` keeps its own `resolveIcon` because it handles the `user:` upload prefix and the generic-art detection that only the banner needs.

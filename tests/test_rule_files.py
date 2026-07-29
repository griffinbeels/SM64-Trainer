# tests/test_rule_files.py
"""The path-scoped rule files must actually reach someone, and stay skimmable.

`.claude/rules/*.md` load only when Claude reads a file matching one of their
`paths:` globs. Two things go wrong silently:

  1. **A glob that matches nothing is a rule that never loads.** The file still
     exists, still looks populated, and nothing reports that it stopped
     reaching anyone. One rename does it. Nobody notices for months, because
     the symptom is a session that simply does not know a thing.

  2. **A rule that grows without bound stops being read.** `.claude/rules/ui.md`
     reached ~26,000 tokens auto-loading on every edit under `ui/` — with a
     single table cell of 18,301 characters, which no reader skims and no
     reviewer checks. It was split four ways on 2026-07-28; the BUDGET below is
     what stops it reassembling itself one appended row at a time.

  3. **A rule file states a number that has quietly stopped being true.**
     `tracking-storage.md` claimed "65 segments … 50 routes" while the corpus
     held 84 and 48 (found 2026-07-28). Prose cannot fail a build, so the
     cardinalities it states are parsed back out and checked against the
     corpus modules below — see CORPUS_CLAIMS.

The budget is deliberately generous — these files carry hard-won evidence and
the answer to crowding is another sub-zone file, not deletion. It fails only
when a file has grown past the point where a reader would still scan it whole.
When it fires, split by path (a narrower `paths:` list), do not summarize:
`docs/architecture.md` is where cross-cutting knowledge goes, and the four
`ui-*.md` files are the worked example of the split.
"""
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RULES = REPO / ".claude" / "rules"
TOOLS = REPO / "tools"

# Chars, not tokens (~4 chars/token). 80k ≈ 20k tokens: comfortably above every
# current file, far below the 103k that made ui.md unreadable.
MAX_CHARS = 80_000

# One table ROW. Above this it stops being a map entry: three rows of 11k–18k
# were lifted into `## sections` below their own tables on 2026-07-28, which is
# where a long narrative belongs — findable by heading, skippable by heading.
MAX_ROW_CHARS = 6_000

# Rule files whose globs are allowed to match nothing YET — a rule written for
# a module that does not exist is a rule for work not yet done. Empty on
# purpose: add a row with a reason, or the guard is decoration.
ALLOWED_EMPTY: dict[str, str] = {}


def rule_files() -> list[Path]:
    return sorted(RULES.glob("*.md"))


def frontmatter_paths(path: Path) -> list[str]:
    """The `paths:` globs, parsed without a YAML dependency.

    The frontmatter shape is fixed and machine-written (`  - "glob"`); a rule
    that deviates fails the shape assertion below rather than being silently
    read as having no globs, which would make this whole guard vacuous.
    """
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path.name}: no YAML frontmatter — it can never be scoped"
    body = match.group(1)
    assert body.lstrip().startswith("paths:"), (
        f"{path.name}: frontmatter does not start with `paths:`. `globs:` is "
        "NOT a recognised key — a rule using it loads unconditionally in every "
        "session while reading as scoped.")
    globs = re.findall(r'^\s*-\s*"([^"]+)"\s*$', body, re.M)
    assert globs, f"{path.name}: `paths:` list is empty"
    return globs


def test_rules_directory_is_not_empty():
    assert rule_files(), "no .claude/rules/*.md — the module map lost its home"


@pytest.mark.parametrize("path", rule_files(), ids=lambda p: p.name)
def test_every_glob_matches_a_real_file(path):
    dead = [glob for glob in frontmatter_paths(path)
            if not any(REPO.glob(glob))]
    if path.name in ALLOWED_EMPTY:
        pytest.skip(ALLOWED_EMPTY[path.name])
    assert not dead, (
        f"{path.name} is scoped to globs that match nothing: {dead}. This rule "
        "never loads — the knowledge in it reaches no session. Fix the glob or "
        "delete the rule; do not leave it looking healthy.")


@pytest.mark.parametrize("path", rule_files(), ids=lambda p: p.name)
def test_no_rule_file_is_too_large_to_read(path):
    size = len(path.read_text(encoding="utf-8"))
    assert size <= MAX_CHARS, (
        f"{path.name} is {size:,} chars (~{size // 4:,} tokens) and auto-loads "
        f"whenever one of its files is opened; the ceiling is {MAX_CHARS:,}. "
        "Split it by path into a narrower sub-zone rule — see the four "
        "ui-*.md files, which came out of one 103k-char file on 2026-07-28. "
        "Do NOT summarize: the evidence in these files is the point.")


@pytest.mark.parametrize("path", rule_files(), ids=lambda p: p.name)
def test_no_single_table_row_is_unskimmable(path):
    """One 18,301-character table cell is how ui.md got where it got.

    A row that long is not a map entry any more, it is an essay wearing a
    table's clothes — and nobody reads it, which is worse than it not existing,
    because its presence reads as the knowledge being available.
    """
    over = [(number, len(text)) for number, text in
            enumerate(path.read_text(encoding="utf-8").split("\n"), 1)
            if text.startswith("| ") and len(text) > MAX_ROW_CHARS]
    assert not over, (
        f"{path.name} has table rows over {MAX_ROW_CHARS:,} chars — too long "
        f"to skim (line, chars): {over}. Lift the narrative into its own "
        "`## section` under the table and leave the row as a pointer to it "
        "(`— **full detail below: [Title](#title)**`), the shape the three "
        "lifted rows already use. Do not summarize to fit.")


def test_claude_md_names_every_rule_file():
    """A rule nobody can find from the map is a rule that gets rewritten."""
    guide = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    missing = [p.name for p in rule_files() if p.name not in guide]
    assert not missing, (
        f"CLAUDE.md's zone table does not name {missing}. The table IS the "
        "index — a session that cannot see a rule file writes the knowledge "
        "somewhere else, which is how the second source of truth starts.")


# --- corpus cardinalities stated in prose, anywhere in the repo ------------
#
# Docstrings and rule files state counts so a reader knows the SHAPE of the
# corpus without opening five tool modules. That is worth keeping — but a
# number in prose is unfalsifiable, and these drifted by 19 segments and 2
# routes without a single red build.
#
# This guard covered ONE file when it was written (2026-07-29) and the final
# whole-branch review found the same drift in six more the same day — which is
# the actual lesson: a stale count is a CLASS, not an incident, so the registry
# below spans every file that states one. Each row is (label, path, regex,
# keys): the regex must match its file EXACTLY once, and its groups must equal
# those keys in `corpus_counts()`.
#
# Adding a count to a docstring means adding a row here. If that feels like
# friction, the alternative is the number nobody can check — which is what
# every row in this list used to be.


def corpus_counts() -> dict[str, int]:
    """The authoritative cardinalities, from the modules the generator composes.

    `build_defaults_seed._build` IS `corpus_legacy.SEGMENTS + MOVEMENTS +
    REDS_TO_PIPE + HUNDRED_COIN_EXITS` and `corpus_routes_main.ROUTES +
    corpus_routes_stage.ROUTES`, so these lengths are the corpus itself rather
    than a second tally kept beside it. Read the seed JSON instead and the
    four segment groups are no longer distinguishable — the categories do not
    partition the same way (four legacy rows are filed Castle Movement).
    """
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import corpus_legacy
    import corpus_movements
    import corpus_routes_main
    import corpus_routes_stage

    counts = {
        "legacy": len(corpus_legacy.SEGMENTS),
        "movements": len(corpus_movements.MOVEMENTS),
        "reds": len(corpus_movements.REDS_TO_PIPE),
        "hundred_coin": len(corpus_movements.HUNDRED_COIN_EXITS),
        "routes_main": len(corpus_routes_main.ROUTES),
        "routes_stage": len(corpus_routes_stage.ROUTES),
    }
    counts["segments"] = (counts["legacy"] + counts["movements"]
                          + counts["reds"] + counts["hundred_coin"])
    counts["routes"] = counts["routes_main"] + counts["routes_stage"]
    # Derived views some prose states directly. `castle_movement` is the seed
    # CATEGORY, which does NOT equal `movements` — four legacy rows and the
    # three reds-to-pipe are filed under it too, which is exactly why the
    # docstring above warns against deriving these groups from the JSON.
    seed = json.loads((REPO / "src" / "sm64_events" / "data"
                       / "defaults.seed.json").read_text(encoding="utf-8"))
    counts["castle_movement"] = sum(
        1 for row in seed["segments"] if row.get("category") == "Castle Movement")
    # The negative pass walks every OTHER movement, so it is one short.
    counts["other_walks"] = counts["movements"] - 1
    # Clause-shape facts that prose states as the REASON a rule works, which is
    # what makes them live claims rather than trivia. `arm_level` is None for
    # an exit that omits `to`, and that single number is why start_origin
    # exists at all — five files say it in five phrasings.
    exits = [trigger for row in seed["segments"]
             for trigger in row["start_triggers"]
             if trigger.get("type") == "level_exit"]
    counts["exits_total"] = len(exits)
    counts["exits_omitting_to"] = sum(1 for t in exits if "to" not in t)
    counts["ends_on_area_enter"] = sum(
        1 for row in seed["segments"]
        if any(t.get("type") == "area_enter" for t in row["end_triggers"]))
    return counts


RULE = ".claude/rules/tracking-storage.md"

CORPUS_CLAIMS = [
    ("rule file: segment corpus", RULE,
     r"(\d+) segments = (\d+) legacy \+ (\d+) movements \+ (\d+) reds-to-pipe"
     r" \+ (\d+) hundred-coin exits",
     ("segments", "legacy", "movements", "reds", "hundred_coin")),
    ("rule file: route corpus", RULE,
     r"(\d+) routes = (\d+) main \+ (\d+) Stage RTA",
     ("routes", "routes_main", "routes_stage")),
    ("defaults.py", "src/sm64_events/tracking/defaults.py",
     r"corpus is now (\d+) segments and (\d+)", ("segments", "routes")),
    ("build_defaults_seed.py", "tools/build_defaults_seed.py",
     r"so (\d+) movement segments", ("movements",)),
    ("corpus_vocab.py", "tools/corpus_vocab.py",
     r"so (\d+) movement segments and (\d+)", ("movements", "routes")),
    ("entities.js", "src/sm64_events/ui/entities.js",
     r"(\d+) of the (\d+) seeded definitions are Castle Movement",
     ("castle_movement", "segments")),
    # practice.js's row is GONE, not moved: main deleted the practice-page
    # route picker (the gesture now lives in the header's RouteRankCard), and
    # the sentence stating the count went with it. Removed rather than
    # re-pointed — the guard's own "zero matches means unchecked" assertion
    # caught this during the merge, which is the behaviour it was written for.
    ("architecture.md: authored", "docs/architecture.md",
     r"route steps and (\d+) definitions were authored", ("movements",)),
    ("architecture.md: negative pass", "docs/architecture.md",
     r"silence across all (\d+) other walks", ("other_walks",)),
    ("architecture.md: route scoping", "docs/architecture.md",
     r"all (\d+) seeded movements carry", ("movements",)),
    ("ui-practice.md: practicedHere sweep", ".claude/rules/ui-practice.md",
     r"all (\d+) seeded definitions × \d+ destination levels", ("segments",)),
    # --- the "exits omitting `to`" family. Five files, five phrasings, one
    # fact: it is the reason start_origin exists, so each states it as live
    # rather than as history. The delta review found them all still on the
    # pre-branch number while the rule file had been corrected — files
    # DISAGREEING is worse than files being stale, because each looks
    # authoritative on its own.
    ("segments.py: exits omitting to", "src/sm64_events/tracking/segments.py",
     r"(\d+) of the (\d+) seeded `level_exit` clauses omit `to`, so the",
     ("exits_omitting_to", "exits_total")),
    ("segments.py: arm-position rule", "src/sm64_events/tracking/segments.py",
     r"\((\d+) of the (\d+) seeded exits omit `to`",
     ("exits_omitting_to", "exits_total")),
    ("synthesize.py", "src/sm64_events/tracking/synthesize.py",
     r"(\d+) of the (\d+) seeded `level_exit` clauses omit `to` because",
     ("exits_omitting_to", "exits_total")),
    ("test_synthesize.py", "tests/test_synthesize.py",
     r"(\d+) of the (\d+) seeded level_exit clauses omit `to`",
     ("exits_omitting_to", "exits_total")),
    ("practicable.py", "src/sm64_events/tracking/practicable.py",
     r"(\d+) of the (\d+) seeded exits omit\n",
     ("exits_omitting_to", "exits_total")),
    # --- the rest of the delta review's finding 5
    ("db.py: default_strat seed", "src/sm64_events/storage/db.py",
     r"The (\d+) castle movements get", ("movements",)),
    ("db.py: reconcile note", "src/sm64_events/storage/db.py",
     r"SEED the (\d+) castle movements", ("movements",)),
    ("api.py: sole-route table", "src/sm64_events/server/api.py",
     r"against all (\d+) definitions in", ("segments",)),
    ("api.py: timeline coverage", "src/sm64_events/server/api.py",
     r"what the (\d+) seeded", ("segments",)),
    ("test_api.py: area_enter ends", "tests/test_api.py",
     r"(\d+) of the (\d+) seeded definitions END on area_enter",
     ("ends_on_area_enter", "segments")),
    ("ui-practice.md: castle-movement share", ".claude/rules/ui-practice.md",
     r"(\d+) of (\d+) seeded rows being Castle Movement",
     ("castle_movement", "segments")),
]


@pytest.mark.parametrize("label,path,pattern,keys", CORPUS_CLAIMS,
                         ids=[row[0] for row in CORPUS_CLAIMS])
def test_the_corpus_counts_stated_here_are_true(label, path, pattern, keys):
    text = (REPO / path).read_text(encoding="utf-8")
    found = re.findall(pattern, text)
    assert len(found) == 1, (
        f"{path} states the {label} {len(found)} times, expected once. This "
        "guard reads the sentence by shape, so rewording it un-pins the "
        "numbers — keep the phrasing, or update CORPUS_CLAIMS in the same "
        "change. Zero matches means the claim is now unchecked.")
    # re.findall yields a bare string for a ONE-group pattern and a tuple for
    # several; zipping the string would iterate its characters and compare
    # digits to counts, so half these rows would silently check nothing.
    groups = found[0] if isinstance(found[0], tuple) else (found[0],)
    assert len(groups) == len(keys), (
        f"{label}: pattern has {len(groups)} groups but {len(keys)} keys")
    truth = corpus_counts()
    stated = {key: int(value) for key, value in zip(keys, groups)}
    wrong = {key: (value, truth[key]) for key, value in stated.items()
             if value != truth[key]}
    assert not wrong, (
        f"{path}'s {label} sentence is out of date (stated, actual): {wrong}. "
        "The corpus is the authority — fix the prose. This is the exact drift "
        "that left seven files claiming 65 segments and 50 routes while the "
        "corpus held 84 and 48.")


def test_the_guards_can_still_fail(tmp_path):
    """Probed in both directions (tests/source_scan.py's rule)."""
    good = tmp_path / "good.md"
    good.write_text('---\npaths:\n  - "CLAUDE.md"\n---\n\n# x\n', encoding="utf-8")
    assert frontmatter_paths(good) == ["CLAUDE.md"]
    assert any(REPO.glob("CLAUDE.md"))
    assert not any(REPO.glob("src/sm64_events/ui/nonexistent_module.js"))

    globs_key = tmp_path / "bad.md"
    globs_key.write_text('---\nglobs:\n  - "*.js"\n---\n', encoding="utf-8")
    with pytest.raises(AssertionError, match="globs"):
        frontmatter_paths(globs_key)

    no_front = tmp_path / "plain.md"
    no_front.write_text("# just a doc\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="frontmatter"):
        frontmatter_paths(no_front)

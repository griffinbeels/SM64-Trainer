# tests/test_single_source.py
"""One derivation, one door — enforced, not aspired to.

"Don't repeat yourself" describes the goal and cannot fail a build. What
actually went wrong on 2026-07-26 was not duplicated CODE: three surfaces each
had their own perfectly reasonable way to turn a star or segment into art (the
practice banner from the segment's start levels, the Rank tab from the entity
key, the pickers from a hand-built context), and the three disagreed. Nobody
copy-pasted anything. Each was written by someone who could not see the other
two, and a reviewer looking at any ONE of them would have approved it.

So the rule this file enforces is narrower and checkable:

  1. ONE module owns the derivation, import-free where it can be, so it is
     unit-testable on its own.
  2. Its public call takes IDENTITY ONLY — never ingredients. Every argument a
     caller has to assemble is a chance to assemble it differently, and this
     bug produced both failure modes: three call sites that built the context
     differently, and then one that passed the context BUILDER where the
     context belonged, silently getting every field's default.
  3. The INGREDIENTS appear nowhere else, and that is what the tests below
     check. Not "is the shared function called" — that passes happily while a
     second path exists beside it. The question is whether a second path can
     be WRITTEN at all: if no other file may name the asset directory or the
     lookup table, there is nothing to derive a competing answer from.

Adding an invariant is one row in INVARIANTS. Adding a new consumer of an
existing one costs nothing — that is the point; the row only pushes back when
someone starts building a second door.

WHAT THIS CANNOT CATCH: a wrong value through the RIGHT door. The context-
builder-passed-as-context bug satisfied every scan here and still repainted an
entire grid, because the shape was right and the value was wrong. Only
rendering finds that, which is why `.claude/rules/ui.md` requires a render for
UI work — the two layers answer different questions and neither substitutes.

Some invariants of this shape are enforced elsewhere, next to the domain they
belong to, and are deliberately NOT duplicated here:
  * `tests/test_ui_caps.py::test_no_call_site_imports_a_style_renderer_directly`
    — rank icon STYLES go through rankicon.js's registry.
  * `tests/test_ui_cap_names.py` — a tier's display name comes from capName().
  * `tests/test_ui_picker_parity.py` — domain vocabulary reaches a control
    through EntityPicker, never a hand-rolled <select>.
"""
from dataclasses import dataclass
from pathlib import Path

import pytest

from source_scan import code_only

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "sm64_events"
UI = SRC / "ui"


@dataclass(frozen=True)
class SingleSource:
    """concept   what has exactly one source of truth
    owners      files allowed to name the ingredients (repo-relative)
    tokens      the ingredients themselves — a distinctive string that only
                appears when someone is building their own answer
    files       every file the rule applies to
    why         the incident, so a future reader can weigh a proposed exception
    """
    concept: str
    owners: frozenset[str]
    tokens: tuple[str, ...]
    files: tuple[Path, ...]
    why: str


def ui_js() -> tuple[Path, ...]:
    return tuple(sorted([*UI.glob("*.js"), *(UI / "components").glob("*.js")]))


def python_sources() -> tuple[Path, ...]:
    return tuple(sorted([*SRC.rglob("*.py"), *(REPO / "tools").rglob("*.py")]))


INVARIANTS = (
    SingleSource(
        concept="star/segment icon art",
        owners=frozenset({"entities.js", "entityicons.js"}),
        tokens=("/ui/assets/star_", "/ui/assets/course_icons/", "/api/icons/file/",
                "COURSE_ICON_PREFIXES", "LEVEL_ICONS", "SPECIAL_COURSE_ICONS",
                "COURSE_SUBSTITUTE_ICONS"),
        files=ui_js(),
        why="Three surfaces derived their own stem and disagreed; BitFS Pipe "
            "Entry drew Bowser on the practice banner and a plain gold star on "
            "the Rank tab (live report 2026-07-26). entities.js::entityIcon is "
            "the chain; entityicons.js is the only layer a component calls.",
    ),
    SingleSource(
        concept="what a rank-up celebration looks like",
        owners=frozenset({"celebrations.js", "rankclimb.js", "hat.js"}),
        tokens=("growProgress", "foldProgress", "flapPhase",
                "--climb-flash", "--climb-settle-scale"),
        files=ui_js(),
        why="ui/celebrations.js is the registry: one entry per effect, "
            "declaring when it fires and what it contributes. rankclimb.js "
            "merges the live ones into one bundle and hat.js reads it. A "
            "component that starts hand-assembling these — a second surface "
            "deciding for itself what a level-up looks like — is exactly the "
            "divergence the icon-art incident above produced, and it would "
            "also defeat the point of the registry, which is that adding the "
            "next celebration is ONE entry (user, 2026-07-27: 'make sure the "
            "system is flexible so that we can add new celebrations / iterate "
            "on this easily as we go').",
    ),
    SingleSource(
        concept="what a rank-up climb DOES, step by step",
        owners=frozenset({"climbplan.js", "climbcurve.js", "climbtuning.js",
                          "celebrations.js", "rankclimb.js", "tune.js"}),
        # QUOTED, the same shape the /api/target invariant below uses. A beat
        # kind only ever appears as a string literal -- in an `on:` list or a
        # comparison -- so quoting the token is what tells it apart from an
        # identifier that merely starts with the same letters.
        tokens=('"tierskip"', '"anticipate"'),
        files=ui_js(),
        why="ui/climbplan.js decides the whole sequence -- which ranks get a "
            "step of their own, which whole tier is condensed into one, and "
            "where the bar is pinned. celebrations.js may NAME those steps "
            "(that is what an `on:` list is) and rankclimb.js walks them; a "
            "third surface naming one is a second opinion about the order a "
            "climb happens in. The live risk is real and already written down: "
            "components/celebrate.js's scope overlay still runs its own "
            "hand-rolled fill->flip->hold machine, so it is exactly the file "
            "that would grow a competing multi-rank sequence.\n"
            "The tokens are QUOTED for a reason found on 2026-07-28: "
            "ui/routeswap.js calls CELEBRATIONS.tierAnticipate/.tierBurst "
            "directly and has to build their `tune` argument using "
            "climbtuning.js's own `anticipateSquash` FIELD NAME, which a bare "
            "substring scan read as the beat kind. The first fix was to add "
            "routeswap.js to `owners` -- which silenced the collision by "
            "exempting the whole file, so a planted beat-kind list in it went "
            "undetected. Matching the quoted literal instead keeps the file "
            "under the guard while letting the tuning vocabulary through: a "
            "field name is an identifier, a beat kind is always a string.",
    ),
    SingleSource(
        concept="the server's TCP port",
        owners=frozenset({"paths.py"}),
        tokens=("8064", "8065"),
        files=python_sources(),
        why="core/paths.py::server_port() answers it for everyone — SM64_PORT "
            "override, else 8064 frozen / 8065 from source, so a dev server and "
            "a built exe can never collide. A second literal anywhere is a "
            "component that binds or probes the wrong one in one of those two "
            "worlds. (Both numbers appear in PROSE in relaunch.py and "
            "single_instance.py, which is why this scans code, not text.)",
    ),
    SingleSource(
        concept="moving the practice target",
        owners=frozenset({"target.js"}),
        tokens=('"/api/target"',),
        files=ui_js(),
        why="POST /api/target can REFUSE since 2026-07-27 (you may only "
            "practice what you are standing in front of -- see "
            "tracking/practicable.py). A bare `await send(...)` inside a click "
            "handler rejects into nothing, so the refusal is invisible and the "
            "button reads as dead; NINE call sites had exactly that shape and "
            "would each have needed their own catch. ui/target.js's "
            "requestTarget does the write, puts the server's own sentence on "
            "screen, and returns whether it landed.",
    ),
    SingleSource(
        concept="whether the player is standing anywhere practicable",
        owners=frozenset({"stagecontext.js"}),
        tokens=("stage.mode", '"stars"', '"bowser_course"', '"arena"',
                '"castle"'),
        files=ui_js(),
        why="The quick-select banner and the Active-target card both ask it, "
            "and answered it separately until 2026-07-27: a new session on the "
            "game's main screen drew 'No course target available' AND, "
            "directly below it, the previous session's Lethal Lava Land star "
            "wearing an ACTIVE TARGET eyebrow. ui/stagecontext.js owns the "
            "mode list, the armed-segment clause and the read of stage.mode "
            "itself; a file reaching for that field is a second surface "
            "deciding what counts as practicing, which is exactly how those "
            "two came apart. stagebanner.js keeps a row per mode, pinned to "
            "the list by tests/test_ui_practice_context.py.",
    ),
    SingleSource(
        concept="the Bowser Reds star/pipe family suffix",
        owners=frozenset({"redsfamily.js"}),
        tokens=('" (Star)"', '" (Pipe)"'),
        files=ui_js(),
        why="Round 2, item 4's star half (live report 2026-07-31): the pinned "
            "card's Star-mode heading read a bare '8 Red Coins' while its own "
            "Pipe-mode sibling already read '8 Red Coins (Pipe)', and the "
            "banner cell's own toggle spelled the suffix a THIRD way inline -- "
            "the exact 'three surfaces each grow their own honest way and "
            "disagree' shape the icon-art incident (2026-07-26) already paid "
            "for. ui/redsfamily.js::familyLabel(baseName, isPipe) is the one "
            "call; stagebanner.js's RedsCell, practice.js's StarSection and "
            "SegmentSection all pass identity (the star's own name + which "
            "half) through it rather than composing the string themselves. "
            "Python owns the SAME two literals independently "
            "(tracking/views.py::STAR_FAMILY_SUFFIX/PIPE_FAMILY_SUFFIX, "
            "load-bearing there for strategy-family filtering) -- "
            "tests/test_cross_language_parity.py compares the two real "
            "constants rather than either restating the other.",
    ),
    SingleSource(
        concept="which Mario actions are the x-cam moment",
        owners=frozenset({"addresses.py"}),
        tokens=("ACT_STAR_DANCE_EXIT", "ACT_STAR_DANCE_WATER",
                "ACT_STAR_DANCE_NO_EXIT"),
        files=python_sources(),
        why="The x-cam moment -- Usamune's own stop condition, and the "
            "difference between a leaderboard-legal star time and an invalid "
            "one -- is 'Mario entered a star dance'. addresses.py::"
            "STAR_DANCE_ACTIONS is that set, and STAR_GRAB_ACTIONS is derived "
            "from it so the two can never drift apart. Naming a MEMBER outside "
            "the registry means assembling a second set by hand, which is what "
            "tools/derive_xcam.py did while the derivation was being found "
            "(2026-08-01): a fourth dance action added to the registry would "
            "have reached the detector and not the probe that validates it, "
            "and the probe would have gone on reporting GATE PASSED.",
    ),
)


def offenders(invariant: SingleSource) -> dict[str, list[str]]:
    """Files outside the owner set that name any of the ingredients."""
    found = {}
    for path in invariant.files:
        if path.name in invariant.owners:
            continue
        body = code_only(path)
        named = [token for token in invariant.tokens if token in body]
        if named:
            found[path.relative_to(REPO).as_posix()] = named
    return found


@pytest.mark.parametrize("invariant", INVARIANTS, ids=lambda i: i.concept)
def test_only_the_owner_may_derive_it(invariant):
    found = offenders(invariant)
    assert not found, (
        f"{invariant.concept} has one source of truth ({', '.join(sorted(invariant.owners))}) "
        f"and these files build their own: {found}\n{invariant.why}")


def test_the_guards_can_still_fail():
    """Probed in both directions (tests/source_scan.py's rule): a comment
    naming an ingredient must not trip a guard, and real code must."""
    # Looked up by CONCEPT, never by index: adding a row shifted these two
    # apart on 2026-07-27 and the probe started asserting the wrong table.
    by_concept = {invariant.concept: invariant for invariant in INVARIANTS}
    icons = by_concept["star/segment icon art"]
    real = code_only(Path("sample.js"), 'const art = "/ui/assets/star_icons/x.png";')
    prose = code_only(Path("sample.js"), "// we used to read COURSE_ICON_PREFIXES here\n")
    assert [token for token in icons.tokens if token in real] == ["/ui/assets/star_"]
    assert not [token for token in icons.tokens if token in prose]

    target = by_concept["moving the practice target"]
    real_js = code_only(Path("sample.js"), 'send("POST", "/api/target", body);')
    prose_js = code_only(Path("sample.js"), '// every write goes through "/api/target"\n')
    assert [token for token in target.tokens if token in real_js] == ['"/api/target"']
    assert not [token for token in target.tokens if token in prose_js]

    port = by_concept["the server's TCP port"]
    real_py = code_only(Path("sample.py"), "PORT = 8064\n")
    docstring_py = code_only(Path("sample.py"), '"""binds :8064 normally."""\nPORT = None\n')
    assert [token for token in port.tokens if token in real_py] == ["8064"]
    assert not [token for token in port.tokens if token in docstring_py]

    climb = by_concept["what a rank-up celebration looks like"]
    real_js = code_only(Path("sample.js"), "const props = { growProgress: 0.5 };\n")
    prose_js = code_only(Path("sample.js"), "// growProgress used to be built here\n")
    assert [token for token in climb.tokens if token in real_js] == ["growProgress"]
    assert not [token for token in climb.tokens if token in prose_js]


def test_every_invariant_actually_covers_files():
    """A row whose glob matches nothing passes forever while guarding nothing —
    this repo has shipped five tests that were green and asserted nothing."""
    for invariant in INVARIANTS:
        assert invariant.files, f"{invariant.concept} scans no files"
        # The owner has to be inside the scanned set, or `owners` is excluding
        # a file that was never going to be scanned and the rule is narrower
        # than it reads.
        names = {path.name for path in invariant.files}
        assert invariant.owners <= names, (
            f"{invariant.concept} exempts {sorted(invariant.owners - names)}, "
            "which its own file set never scans")


import re

# code_only()'s Python branch (source_scan.py::python_code) joins every kept
# TOKEN with "\n" -- deliberately, so a docstring or comment can never fake an
# adjacency a formatter didn't put there. The side effect: a literal substring
# search for `row["strat_tag"]` can never match ANYTHING, real or fake, since
# that expression is four tokens (`row`, `[`, `"strat_tag"`, `]`) and the
# joined form always has a newline between them. This regex matches the same
# SHAPE against the newline-per-token form instead -- any identifier
# subscripted by the literal key "strat_tag" -- which also means the
# variable's own name (row, r, entry, ...) never matters, only the pattern.
_STRAT_TAG_SUBSCRIPT = re.compile(r'\w+\n\[\n"strat_tag"\n\]')


def _keys_a_row_by_strat_tag(path: Path) -> bool:
    return bool(_STRAT_TAG_SUBSCRIPT.search(code_only(path)))


def test_only_views_reads_the_pbs_table_for_a_rank():
    """Which of my saved times a rank grades has exactly ONE answer, and it is
    `views.current_pbs_by_strat`. It grew a second one in tracking/marelo.py
    -- min() over raw attempts -- which paid MARELO out before the user
    clicked Save as PB and survived every test for weeks (task 0034).

    Files allowed to key a pb row by strategy at all: views.py (the door) and
    marelo.py (which calls it). A third file doing its own latest-row-wins
    scan is the bug coming back.

    service.py is exempted too, but for a DIFFERENT reason than views/marelo:
    `undo_pb` reads `row["strat_tag"]` off the ONE row it just deleted, only
    to echo it back into the `pb_undone` broadcast payload -- there is no
    scan, no per-strategy keying, nothing this guard is protecting against.
    Excluding it by name (rather than loosening the pattern) keeps the check
    honest: a SECOND legitimate reason to touch this field is still only one
    file wide, and a new one shows up in a diff of this list."""
    offenders = []
    for path in (SRC / "tracking").glob("*.py"):
        if path.name in ("views.py", "marelo.py", "service.py"):
            continue
        if _keys_a_row_by_strat_tag(path) and "pbs" in code_only(path):
            offenders.append(path.name)
    for path in (SRC / "ranks").glob("*.py"):
        if _keys_a_row_by_strat_tag(path):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} reads pb rows directly. Rank grading goes through "
        "tracking/views.py::current_pbs_by_strat -- see task 0034.")


def test_the_pb_door_guard_can_still_fail():
    # Probed in both directions, through the SAME code_only() the real guard
    # uses (tests/source_scan.py's rule) -- a bare substring check on raw text
    # would trip on a comment mentioning the pattern, which is not what the
    # guard above actually does, and (as this file's own history shows) a
    # naive literal substring check against code_only()'s newline-per-token
    # form can never match real code at all.
    real = code_only(Path("sample.py"),
                     'for row in db.pbs():\n    x = row["strat_tag"]\n')
    prose = code_only(Path("sample.py"),
                      '# never read row["strat_tag"] off the pbs table here\n')
    assert _STRAT_TAG_SUBSCRIPT.search(real) and "pbs" in real
    assert not _STRAT_TAG_SUBSCRIPT.search(prose)


def test_no_file_reads_a_fragment_off_the_h_function():
    """`h.Fragment` is `undefined` in the vendored Preact, so htm's
    `<${h.Fragment}>` calls `h(undefined, ...)` and Preact's diff then runs
    `document.createElement(undefined)` -- a literal `<undefined>` element
    wrapping everything inside it.

    It is invisible in the common case and fatal in the specific one: it
    silently breaks any `>` child selector and any flex/grid parent
    relationship. `.context-select > select` was exactly that, and the header
    card's whole hit target died until a five-point elementFromPoint sweep
    caught it (2026-07-28). Three shipped call sites carried the same spelling
    for months -- entitymodal.js's EntityPicker and strategystep.js's two --
    which is why this is a scan rather than a comment.

    Import `Fragment` by NAME. Comments naming the broken spelling are fine
    (contextselect.js explains it), which is why this reads code_only."""
    offenders = sorted(path.name for path in UI.rglob("*.js")
                       if "vendor" not in path.parts
                       and "h.Fragment" in code_only(path))
    assert not offenders, (
        f"{offenders} use `h.Fragment`, which is undefined in the vendored "
        "Preact and renders a literal <undefined> element. Import Fragment "
        "by name from 'preact' instead.")


def test_the_fragment_guard_can_still_fail():
    """Probed both ways: a comment explaining the bug must not trip it, and
    real code must."""
    scan = lambda text: "h.Fragment" in text
    assert scan("return html`<${h.Fragment}>x<//>`")
    assert not scan("// never write h dot Fragment here")

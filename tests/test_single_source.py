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
    icons = INVARIANTS[0]
    real = code_only(Path("sample.js"), 'const art = "/ui/assets/star_icons/x.png";')
    prose = code_only(Path("sample.js"), "// we used to read COURSE_ICON_PREFIXES here\n")
    assert [token for token in icons.tokens if token in real] == ["/ui/assets/star_"]
    assert not [token for token in icons.tokens if token in prose]

    target = INVARIANTS[2]
    real_js = code_only(Path("sample.js"), 'send("POST", "/api/target", body);')
    prose_js = code_only(Path("sample.js"), '// every write goes through "/api/target"\n')
    assert [token for token in target.tokens if token in real_js] == ['"/api/target"']
    assert not [token for token in target.tokens if token in prose_js]

    port = INVARIANTS[1]
    real_py = code_only(Path("sample.py"), "PORT = 8064\n")
    docstring_py = code_only(Path("sample.py"), '"""binds :8064 normally."""\nPORT = None\n')
    assert [token for token in port.tokens if token in real_py] == ["8064"]
    assert not [token for token in port.tokens if token in docstring_py]


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

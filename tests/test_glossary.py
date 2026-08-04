"""The glossary's own rules, made into a gate (tools/check_glossary.py).

docs/glossary.md defines every domain noun this project invented. Two
properties make it worth keeping rather than merely writing:

  closed       every domain word used inside a definition has its own row
  active voice no definition hides the actor, because the actor is the module

Neither property survives on good intentions, so both are checked here. The
fixtures under tests/fixtures/glossary_*.md are deliberately tiny: each rule is
mutation-proved by editing one of them and watching exactly one test go red.
"""
import importlib.util
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "check_glossary.py"
_spec = importlib.util.spec_from_file_location("check_glossary", _TOOL)
_mod = importlib.util.module_from_spec(_spec)
# Register BEFORE exec_module. The tool defines dataclasses under `from
# __future__ import annotations`, so @dataclass resolves each string annotation
# via sys.modules[cls.__module__] -- which is None for a module loaded by path
# alone, and the failure is an AttributeError at collection time that aborts the
# whole suite rather than one test.
sys.modules["check_glossary"] = _mod
_spec.loader.exec_module(_mod)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_the_real_glossary_has_no_unexplained_findings():
    """The gate. Every rule below was mutation-proved against a fixture first,
    so a green run here means the rules held, not that they were toothless."""
    glossary = REPO_ROOT / "docs" / "glossary.md"
    findings = _mod.run(glossary.read_text(encoding="utf-8"), REPO_ROOT)
    assert findings == [], "\n".join(f"{f.rule}: {f.term}: {f.detail}" for f in findings)


def test_every_allowlist_entry_carries_a_reason():
    for pair, reason in _mod.ALLOWED.items():
        assert reason.strip(), f"{pair} is exempted with no reason"


def test_the_glossary_defines_the_words_we_say_every_session():
    """Closure keeps the glossary self-consistent but cannot notice a word
    missing from it entirely -- an empty glossary is perfectly closed. This is
    the floor: the nouns that appear in nearly every conversation about this
    project."""
    rows = {r.term.lower() for r in _mod.parse((REPO_ROOT / "docs" / "glossary.md").read_text(encoding="utf-8"))}
    for spoken in (
        "target", "star", "segment", "attempt", "strategy", "personal best",
        "rank", "standard", "marelo", "selector", "objective card", "caveat",
        "journal", "projector", "detector",
    ):
        assert spoken in rows, f"'{spoken}' is a word we say every session and has no row"


def test_parse_reads_term_definition_and_lives():
    rows = _mod.parse(_fixture("glossary_ok.md"))
    assert [row.term for row in rows] == ["Journal", "Event"]
    journal = rows[0]
    assert journal.section == "What runs"
    assert journal.definition.startswith("The append-only file")
    assert "core/uilog.py" in journal.lives
    assert journal.contrast is None


def test_lives_path_that_does_not_exist_is_a_finding():
    rows = _mod.parse(_fixture("glossary_bad_path.md"))
    findings = _mod.check_lives_paths(rows, REPO_ROOT)
    assert [(f.rule, f.term) for f in findings] == [("lives-path", "Journal")]


def test_lives_paths_that_exist_produce_no_findings():
    rows = _mod.parse(_fixture("glossary_ok.md"))
    assert _mod.check_lives_paths(rows, REPO_ROOT) == []


def test_unresolved_mark_and_unmarked_use_are_both_findings():
    rows = _mod.parse(_fixture("glossary_unclosed.md"))
    reported = [(f.rule, f.term) for f in _mod.check_closure(rows)]
    assert ("mark-unresolved", "Event") in reported
    assert ("unmarked-use", "Journal") in reported


def test_a_closed_glossary_produces_no_closure_findings():
    rows = _mod.parse(_fixture("glossary_ok.md"))
    assert _mod.check_closure(rows) == []


def test_a_plural_mark_resolves_to_its_singular_row():
    rows = _mod.parse(
        "## S\n\n### Event\n\nA thing.\n\n"
        "- **Lives** — x (`pyproject.toml`)\n\n"
        "### Journal\n\nHolds every [[events]].\n\n"
        "- **Lives** — y (`pyproject.toml`)\n"
    )
    assert [f for f in _mod.check_closure(rows) if f.rule == "mark-unresolved"] == []


def test_passive_voice_is_a_finding():
    rows = _mod.parse(_fixture("glossary_passive.md"))
    assert [(f.rule, f.term) for f in _mod.check_passive(rows)] == [("passive-voice", "Journal")]


def test_active_voice_produces_no_findings():
    rows = _mod.parse(_fixture("glossary_ok.md"))
    assert _mod.check_passive(rows) == []


def test_the_allowlist_subtracts_only_the_pair_it_names():
    findings = [_mod.Finding("passive-voice", "Journal", 3, "is written")]
    assert _mod.subtract_allowed(findings, {("passive-voice", "Journal"): "quoting a spec"}) == []
    assert _mod.subtract_allowed(findings, {("passive-voice", "Event"): "unrelated"}) == findings


def test_a_row_may_use_its_own_name_unmarked():
    """Otherwise every definition would have to refer to itself in the third
    person, which is exactly the stilted prose the active-voice rule exists to
    prevent."""
    rows = _mod.parse(
        "## S\n\n### Journal\n\nThe journal holds things.\n\n"
        "- **Lives** — x (`pyproject.toml`)\n"
    )
    assert _mod.check_closure(rows) == []

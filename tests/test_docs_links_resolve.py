# tests/test_docs_links_resolve.py
"""Nothing tracked in this PUBLIC repo may point at a file only this machine has.

`docs/superpowers/` (specs and plans), `.tasks/` (the backlog), `internal_notes/`
and `.planning/` are working directories: useful, kept on disk, deliberately
NOT shared — they quote the author directly and name unrelated projects.

The failure mode this test exists for is quiet and one-directional. Adding a
path to `.gitignore` does not touch the eleven files that already cite it, and
nothing complains: the author still has every one of those files, so every link
still resolves for the only person who would notice. It resolves for nobody
else. That is exactly what happened when `docs/superpowers/` was ignored on
2026-07-27 — `docs/architecture.md` was left citing nine specs as the
authoritative record of decisions, plus two in `tools/corpus_routes_*.py` and
one in `memory/addresses.py`, all of them dead in a fresh clone (found
2026-07-28).

The fix is never to re-track the file. It is to say what the fact IS, and name
the tracked thing that holds it — a module docstring, a test, this
architecture doc. A pointer into a private directory is not evidence; it is a
promise that the evidence exists somewhere.

Prose ABOUT the convention is allowed, and needed: `.gitignore` explains the
rule, and `docs/architecture.md`'s header tells a reader why specs are absent.
Those are declared below by path, one line each, rather than pattern-matched —
an exemption someone has to add on purpose is an exemption someone notices.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Directories that are gitignored on purpose and must never be cited as a
# source by anything tracked.
PRIVATE_DIRS = ("docs/superpowers/", "internal_notes/", ".planning/",
                ".tasks/", ".superpowers/")

# Files allowed to name a private directory, because naming it IS their job.
# (path, why) — add a row consciously; do not widen this into a glob.
EXEMPT = {
    ".gitignore": "declares the rule",
    "docs/architecture.md": "its header explains why specs are absent, and "
                            "names the directory once to do so",
    "tests/test_docs_links_resolve.py": "this file",
    "AGENTS.md": "may need to tell Codex where the local backlog lives",
    "CLAUDE.md": "may need to tell a session where the local backlog lives",
    ".claude/skills/release/SKILL.md":
        "names internal_notes/ as a place to WRITE a scratch notes file, and "
        "says why (gitignored, so it cannot dirty the release preflight's "
        "clean-tree check). A write target is not a dead citation.",
}

# Text files worth scanning. Binary and vendored trees are skipped.
SCAN_SUFFIXES = {".md", ".py", ".js", ".html", ".json", ".toml", ".bat", ".txt"}


def tracked_text_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout.split("\n")
    return [p for p in out
            if p and Path(p).suffix in SCAN_SUFFIXES and "/vendor/" not in p]


def citations(path: str) -> list[str]:
    """Private-directory paths named by a tracked file, one per hit."""
    try:
        text = (REPO / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return []
    return [directory for directory in PRIVATE_DIRS if directory in text]


@pytest.mark.parametrize("path", tracked_text_files())
def test_no_tracked_file_cites_a_private_working_directory(path):
    if path in EXEMPT:
        pytest.skip(f"exempt: {EXEMPT[path]}")
    named = citations(path)
    assert not named, (
        f"{path} cites {named}, which is gitignored — the link is dead in "
        "every clone but the author's. State the FACT and name a tracked file "
        "that carries it (a module docstring, a test, docs/architecture.md). "
        "Do not re-track the private file.")


def test_exemptions_all_point_at_files_that_exist():
    """An exemption for a deleted file quietly widens as paths get reused."""
    missing = [path for path in EXEMPT if not (REPO / path).exists()]
    assert not missing, f"EXEMPT names files that no longer exist: {missing}"


def test_every_private_dir_is_actually_ignored():
    """If a directory stops being ignored, this whole guard is theatre."""
    for directory in PRIVATE_DIRS:
        probe = f"{directory}probe-file.md"
        result = subprocess.run(["git", "check-ignore", "-q", probe],
                                cwd=REPO, capture_output=True)
        assert result.returncode == 0, (
            f"{directory} is NOT gitignored, but this test treats it as "
            "private. Either ignore it or drop it from PRIVATE_DIRS — a guard "
            "protecting a directory that is already public protects nothing.")


def test_the_guard_can_still_fail(tmp_path):
    """Probed in both directions (tests/source_scan.py's rule)."""
    live = tmp_path / "live.md"
    live.write_text("see docs/superpowers/specs/x-design.md for the rationale",
                    encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text("see tracking/segments.py's docstring", encoding="utf-8")

    def named(path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        return [d for d in PRIVATE_DIRS if d in text]

    assert named(live) == ["docs/superpowers/"]
    assert named(clean) == []

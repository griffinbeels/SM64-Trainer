"""The rendered gate must not disable itself.

Every browser test skips its whole module when `find_uilab()` cannot put uilab
on `sys.path`, and `tools/run_tests.py` prints a warning and still exits 0. On
2026-09-17 that combination hid 321 rendered tests in every `.codex/<feature>`
worktree, because the resolver only understood `.claude/worktrees/<slug>`; the
full gate escaped it only because `tools/verify_full.py` sets `UILAB_PATH`
itself. A silent gate is worse than a missing one.

This costs no browser: it asks whether the resolver finds what is on disk.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import _primary_checkout, find_uilab  # noqa: E402


def test_the_primary_checkout_is_resolved_through_git_not_folder_depth():
    """A worktree's own parent is not where sibling checkouts live."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
    assert _primary_checkout() == Path(common.stdout.strip()).parent


def test_uilab_on_disk_is_found_from_this_checkout(monkeypatch):
    """With a sibling uilab present, the gate must be ON. A machine without
    uilab has nothing to find, and reports that instead."""
    monkeypatch.delenv("UILAB_PATH", raising=False)
    sibling = _primary_checkout().parent / "uilab" / "uilab" / "__init__.py"
    missing = find_uilab()
    if not sibling.is_file():
        assert missing and "uilab not found" in missing
        return
    assert missing is None, missing

# tests/test_chains.py
"""The chain files must still describe a pipeline that exists.

A chain file (`.claude/rules/chain-*.md`) names, hop by hop, where one value is
true, which module owns it there, the probe that reads it and the injection
point that forces it. Every one of those names rots the same silent way a
`paths:` glob does: the file still reads as authoritative while pointing at a
module somebody renamed, so the next diagnosis walks a map of a system that is
no longer there. `~/.claude/knowledge/tools/check_chain.py` is the machine-wide
checker for that, and this is the wiring that makes a rename go red here.

The second assertion is the one that matters more. The checker EXITS 0 when it
finds no chain files at all — reasonable for a repo that has none, and a
vacuous pass for this one, which is exactly the false-green shape
`tests/test_rule_files.py` was written for (a glob matching nothing) and
`tests/test_docs_links_resolve.py` after it. So the count is asserted here
separately, and it does not depend on the checker being installed.

What no check can catch: a hop nobody drew. The two "when broken" columns are
where that tends to surface, and they are a reader's job, not a test's.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECKER = Path.home() / ".claude" / "knowledge" / "tools" / "check_chain.py"


def chain_files() -> list[Path]:
    return sorted((REPO / ".claude" / "rules").glob("chain-*.md"))


def test_the_repo_has_at_least_one_chain_file():
    """Without this the checker below passes over nothing and says PASS."""
    assert chain_files(), (
        "no .claude/rules/chain-*.md in this repo. The checker exits 0 on an "
        "empty set, so deleting the last chain file would leave this suite "
        "green while the pipeline documentation is gone.")


def test_every_chain_file_resolves_end_to_end():
    chains = chain_files()
    assert chains, "no chain files — see the test above"
    if not CHECKER.exists():
        pytest.skip(
            f"the machine-wide chain checker is not installed at {CHECKER}; "
            "it lives in the knowledge repo (junctioned to ~/.claude/knowledge)")
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--repo", str(REPO)],
        capture_output=True, text=True, check=False)
    failed = [line for line in result.stdout.splitlines()
              if line.startswith("FAIL") or line.strip().startswith("- ")]
    assert not failed and result.returncode == 0, (
        "a chain file names something that no longer resolves — a module, a "
        "probe script, an injection point, or a `paths:` glob that now matches "
        "nothing. Fix the name in the chain file in the same change that moved "
        f"the hop.\n{result.stdout}{result.stderr}")
    assert "PASS" in result.stdout, (
        "the checker ran and reported neither PASS nor FAIL for "
        f"{len(chains)} chain file(s) — it found nothing to check:\n"
        f"{result.stdout}{result.stderr}")

"""Match corpus for no-merge-dirty-worktree.py.

Run after ANY edit to the guard:
`uv run python .claude/hooks/tests/test_no_merge_dirty_worktree.py`

Exercises the COMMAND parse only -- which commands name a branch being merged
IN -- because that half is deterministic and the other half (is that branch's
worktree dirty right now) is the machine's state. The first ALLOW case is the
real command from 2026-09-01: `git merge-base main feature/console-support`
matched `merge\\b` (a word boundary sits between `merge` and `-`) and was
blocked as a merge of a branch with uncommitted work, when it read nothing.
"""
import importlib.util
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "no-merge-dirty-worktree.py"


def load_guard():
    spec = importlib.util.spec_from_file_location("no_merge_dirty_worktree", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def merged_branch(guard, command):
    """The branch the guard believes `command` merges in, or None."""
    match = guard._MERGE.search(command)
    if not match:
        return None
    return guard._branch_operand(match.group("rest"))


# command -> the branch the guard must see (None = must not fire)
CASES = {
    # Read-only git subcommands that merely START with "merge"
    "git merge-base main feature/console-support": None,
    "git merge-base main d0cc98f6 | xargs git log -1": None,
    "git merge-file a b c": None,
    "git merge-tree main feature/x": None,
    "git diff main feature/console-support --stat": None,
    # No branch operand at all
    "git merge --abort": None,
    "git merge --continue": None,
    # The real merges the guard exists for
    "git merge feature/console-support": "feature/console-support",
    "git merge --no-ff feature/x": "feature/x",
    "cd .claude/worktrees/tmp && git merge --no-ff feature/x": "feature/x",
    "git -C C:/repo merge feature/x": "feature/x",
}


def main() -> int:
    guard = load_guard()
    failures = []
    for command, expected in CASES.items():
        got = merged_branch(guard, command)
        if got != expected:
            failures.append(f"  {command!r}: expected {expected!r}, got {got!r}")
    if failures:
        print("no-merge-dirty-worktree parse corpus FAILED:")
        print("\n".join(failures))
        return 1
    print(f"no-merge-dirty-worktree parse corpus OK ({len(CASES)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Refuse `git merge <branch>` while THAT branch's worktree has uncommitted work.

2026-07-29, sm64_tracker: a feature worktree's suite was green, so the merge
went ahead and `main` was fast-forwarded -- and `main` broke on collection. Two
test files had been ported onto uilab and a new resolver module created beside
them, but never committed. The merge carried the pre-port version. The green
suite was real; it measured the WORKING TREE, and the merge takes the COMMIT.
(That resolver has since been folded into the one door, `tools/find_uilab.py`,
so it is described here rather than named -- a docstring citing a path is a dead
pointer the day the path moves.)

No amount of re-running tests can catch this, which is why it is a hook rather
than a note: the evidence that would expose it is `git status --porcelain` in a
DIFFERENT directory than the one the suite ran in, and nothing prompts you to
look there.

Fails OPEN on anything it cannot parse. A guard that bricks git is worse than
the bug it prevents.

Exit 2 blocks and shows stderr to the model; exit 0 allows.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

# `git merge`, allowing a leading `cd ... &&` and any flags. Deliberately does
# NOT match `git merge --abort/--continue/--quit` (no branch operand at all).
_MERGE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?merge\b(?P<rest>[^&|;]*)")
_TERMINAL = {"--abort", "--continue", "--quit", "--no-commit"}


def _branch_operand(rest: str) -> str | None:
    """The branch being merged IN, or None when there isn't one."""
    tokens = [t for t in rest.split() if t]
    if any(t in _TERMINAL for t in tokens):
        return None
    operands = [t for t in tokens if not t.startswith("-")]
    # `git merge -m "msg" branch` -- the message is consumed by -m, and we
    # only ever take the LAST bare token, so a quoted message cannot be it
    # unless it is also the only one. Bail rather than guess in that case.
    return operands[-1] if len(operands) == 1 else (operands[-1] if operands else None)


def _worktrees() -> dict[str, str]:
    """{branch-name: worktree path} for every checked-out branch."""
    out = subprocess.run(["git", "worktree", "list", "--porcelain"],
                         capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        return {}
    trees: dict[str, str] = {}
    path = None
    for line in out.stdout.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):].strip()
        elif line.startswith("branch ") and path:
            trees[line[len("branch refs/heads/"):].strip()] = path
    return trees


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    match = _MERGE.search(command)
    if not match:
        return 0
    branch = _branch_operand(match.group("rest"))
    if not branch:
        return 0

    try:
        path = _worktrees().get(branch)
    except Exception:
        return 0
    if not path:
        return 0            # not checked out anywhere -> nothing uncommitted

    try:
        status = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                                capture_output=True, text=True, timeout=15)
    except Exception:
        return 0
    if status.returncode != 0 or not status.stdout.strip():
        return 0

    dirty = status.stdout.strip().splitlines()
    listed = "\n  ".join(dirty[:10])
    if len(dirty) > 10:
        listed += f"\n  ...and {len(dirty) - 10} more"
    sys.stderr.write(
        f"BLOCKED: {branch!r} has uncommitted work in its worktree, so the "
        f"merge would carry an OLDER tree than the one you verified.\n"
        f"  {path}\n  {listed}\n"
        "A green suite there measured the working tree; a merge takes the "
        "commit. Commit (or stash) those first, then merge.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())

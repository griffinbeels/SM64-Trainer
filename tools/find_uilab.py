"""Make `uilab` importable, or say why it is not.

uilab is a machine-level dev module (https://github.com/griffinbeels/uilab)
shared by every project on this machine. It is not — and cannot be — in this
project's lockfile, since it is installed from a local checkout.

ONE door, because two resolvers drift: `tests/test_responsive.py` and
`tools/measure_objective_card.py` both need it, and any future rig will too.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _primary_checkout() -> Path:
    """The checkout every worktree of this repo shares, or this one."""
    import subprocess
    try:
        common = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return REPO
    return Path(common).parent if common else REPO


def find_uilab() -> str | None:
    """Put uilab on sys.path and return None, or return a message explaining why not.

    An editable install alone is NOT enough, and that is the whole reason this
    function exists. uilab is absent from the lockfile, so `uv sync` — which
    `uv run` performs implicitly — PRUNES it. Measured 2026-07-28: `import
    uilab` succeeded, then a later `uv run pytest` removed the package and the
    layout gate silently became a skip. A gate that disappears when a package
    manager tidies up is a green-forever failure.

    So resolve by PATH, which no sync can undo: an installed copy if one is
    there, else `UILAB_PATH`, else a sibling checkout.

    "Sibling" is computed from the PRIMARY checkout, not from this file's
    directory: every branch is a git worktree, so the worktree's own sibling is
    not where uilab lives. GIT owns that identity -- `--git-common-dir` names
    the primary checkout's `.git` whatever the layout is -- and a hard-coded
    folder depth does not. The `.claude/worktrees/<slug>` rule resolved
    (2026-07-28), while every `.codex/<slug>` worktree skipped the whole
    rendered gate and the run still exited 0 (measured 2026-09-17, the same
    resolution `tools/verify_full.py` already used).
    """
    import importlib.util

    if importlib.util.find_spec("uilab") is not None:
        return None
    repo = _primary_checkout()
    candidates = []
    if os.environ.get("UILAB_PATH"):
        candidates.append(Path(os.environ["UILAB_PATH"]))
    candidates.append(repo.parent / "uilab")
    for candidate in candidates:
        if (candidate / "uilab" / "__init__.py").exists():
            sys.path.insert(0, str(candidate))
            return None
    return (
        "uilab not found. It is an optional dev module shared across this "
        "machine's projects (https://github.com/griffinbeels/uilab); a fresh "
        "clone will not have it, and everything except the rendered layout "
        "gates runs without it. To enable them, clone it beside this repo (or "
        "set UILAB_PATH) and install its browser:\n"
        "    git clone https://github.com/griffinbeels/uilab\n"
        "    uv run python -m playwright install chromium\n"
        f"looked in: {', '.join(str(c) for c in candidates)}")

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

    "Sibling" is computed from the REPO, not from this file's directory,
    because this project's rule is that every branch is a git worktree under
    `.claude/worktrees/<slug>` — two levels deeper, where the sibling is
    `.claude/worktrees/uilab` and does not exist. Resolving from the file alone
    made the gate skip in every worktree while passing in the primary checkout
    (measured 2026-07-28).
    """
    import importlib.util

    if importlib.util.find_spec("uilab") is not None:
        return None
    repo = REPO
    for parent in repo.parents:
        if parent.name == "worktrees" and parent.parent.name == ".claude":
            repo = parent.parent.parent
            break
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

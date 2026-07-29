"""Make `uilab` importable, or say why it is not — for the tests that drive a
real browser.

`tests/test_responsive.py` carries its own copy of this because it predates the
second and third consumers. Lifting it here rather than importing it from that
module is deliberate: that file is a pytest module with a `pytest.skip(...,
allow_module_level=True)` at import time, so importing anything FROM it would
skip the importer too. Folding its copy into this one is a follow-up worth
doing, and it is a one-line change there.

The reasoning below is that file's, verbatim, because it was measured rather
than assumed and shortening it would lose the measurement.
"""
import os
import sys
from pathlib import Path


def find_uilab() -> str | None:
    """Return None once uilab is importable, else a sentence explaining why.

    An editable install alone is NOT enough, and that is the whole reason this
    exists. uilab is not in this project's lockfile — it cannot be, since it is
    installed from a local checkout — so `uv sync`, which `uv run` performs
    implicitly, PRUNES it. Measured 2026-07-28: `import uilab` succeeded, then
    a later `uv run pytest` removed the package and the layout gate silently
    became a skip. A gate that disappears when a package manager tidies up is
    exactly the green-forever failure these tests are written to avoid.

    So resolve it by PATH, which no sync can undo: an installed copy if one is
    there, else a sibling checkout, else `UILAB_PATH`.

    "Sibling" is computed from the REPO, not from this file, because every
    branch here is a git worktree under `.claude/worktrees/<slug>` — two levels
    deeper, where the sibling of the tree does not exist. Resolving from the
    file alone made the gate skip in every worktree while passing in the
    primary checkout.
    """
    import importlib.util

    if importlib.util.find_spec("uilab") is not None:
        return None
    repo = Path(__file__).resolve().parents[1]
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
        "machine's projects; a fresh clone will not have it, and everything "
        "except the browser-driven gates runs without it. To enable them, "
        "clone it beside this repo (or set UILAB_PATH) and install its "
        "browser:\n"
        "    git clone https://github.com/griffinbeels/uilab\n"
        "    uv run python -m playwright install chromium\n"
        f"looked in: {', '.join(str(c) for c in candidates)}")

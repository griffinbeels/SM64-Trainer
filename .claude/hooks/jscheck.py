#!/usr/bin/env python3
r"""PostToolUse guard: `node --check` any .js file just written or edited.

WHY: the UI is hand-written htm template literals (`html\`...\``) with nested
`${...}`. A single unbalanced backtick or brace is a SyntaxError that blanks the
ENTIRE page on load — and pytest cannot catch it (the UI is JS, served raw, no
JS test harness). It shipped once (Phase C run-view tail: `</div>}\`;` instead of
`</div>\`}\`;`) and was only caught by a browser smoke test, after the fact.

This fires on every Write/Edit of a .js file and runs `node --check` on it, so a
syntax error is surfaced AT the edit — before a commit or a browser smoke test.
Exit 2 + stderr tells the model to fix it (PostToolUse: the write already
happened; this signals the model to correct before moving on).

FAILS OPEN (exit 0) on anything unexpected — no node on PATH, unparseable
payload, non-.js path, vendored lib, or check-runner error — so the guard can
never brick the Write/Edit tool. Worst case = the status quo without the hook.
"""
import json
import shutil
import subprocess
import sys


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0  # fail open: unparseable payload
    path = (data.get("tool_input") or {}).get("file_path") or ""
    norm = path.replace("\\", "/")
    if not norm.endswith(".js"):
        return 0  # only guard JS
    if "/vendor/" in norm:
        return 0  # vendored/minified libs are not ours to check
    node = shutil.which("node")
    if not node:
        return 0  # fail open: no node available
    try:
        r = subprocess.run([node, "--check", path],
                           capture_output=True, text=True, timeout=15)
    except Exception:
        return 0  # fail open: runner error
    if r.returncode != 0:
        sys.stderr.write(
            f"node --check FAILED on {path}:\n{r.stderr.strip()}\n\n"
            "Fix the JS syntax before continuing. A blanked UI is almost always "
            "an unbalanced htm template-literal backtick or ${...} — check the "
            "nesting around the reported line.\n")
        return 2  # signal the model to self-correct
    return 0


if __name__ == "__main__":
    sys.exit(main())

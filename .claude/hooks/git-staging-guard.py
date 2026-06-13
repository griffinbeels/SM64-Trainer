#!/usr/bin/env python3
"""PreToolUse(Bash) guard — block over-broad git staging in this SHARED checkout.

WHY THIS EXISTS (the evidence, so a future editor doesn't "simplify" it away):
This repo is a single working tree that concurrent Claude sessions share at the
same time (see CLAUDE.md "Parallel work zones" and the concurrent-sessions
memory). A create-artifacts audit measured the project's recurring mistakes
across ~25 session transcripts; the single most expensive class was over-broad
staging — `git add -A` / `git add .` / `git commit -a` — which sweeps OTHER
sessions' uncommitted work into a commit on the wrong branch. It recurred in 8+
sessions and had only ever been written up as memory PROSE, which the same audit
showed was never recalled. A hook is the right home for a mechanical rule: it
fires at the decision point, every time, regardless of what's in context.

CONTRACT
- stdin: the PreToolUse JSON payload ({"tool_input": {"command": "..."}, ...}).
- exit 0  -> allow (the default for anything that isn't over-broad git staging).
- exit 2  -> block; stderr is shown to the model so it can self-correct.
- Any parse/Unknown failure -> exit 0. A guard must never brick the Bash tool;
  failing open is the safe direction (worst case: the mistake it prevents is
  back, which is the status quo without the hook).

FALSIFIABILITY: if this ever blocks a legitimate command, the matched substring
is printed — widen the negative lookahead or strip more shell context. If it
ever MISSES an over-broad stage that lands foreign files in a commit, add the
form to ADD/COMMIT below and (ideally) a case to tests/ if one exists.
"""
import json
import re
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # never block on a malformed/empty payload

    cmd = ((payload.get("tool_input") or {}).get("command") or "")
    if "git" not in cmd:
        return 0  # fast path: not a git command at all

    # Strip quoted substrings so flags mentioned INSIDE a commit message
    # (e.g. git commit -m "fix: handle the -a flag") cannot false-trigger.
    bare = re.sub(r"\"[^\"]*\"|'[^']*'", "", cmd)

    # Over-broad STAGING: -A / --all / -a / a bare "." path argument.
    add = re.compile(r"\bgit\s+add\b[^\n|&;]*?(\s-A\b|\s--all\b|\s-a\b|\s\.(?:\s|$))")
    # Over-broad COMMIT: a short-flag cluster containing 'a' (-a, -am, ...) or
    # --all. The (?!-) keeps --amend / --allow-empty / --author OUT, and the
    # \b after --all keeps --allow-* OUT.
    commit = re.compile(r"\bgit\s+commit\b[^\n|&;]*?(\s-(?!-)[A-Za-z]*a[A-Za-z]*\b|\s--all\b)")

    hit = add.search(bare) or commit.search(bare)
    if not hit:
        return 0

    matched = hit.group(0).strip()
    sys.stderr.write(
        "BLOCKED: over-broad git staging in a shared checkout.\n"
        f"  matched: …{matched}\n"
        "  This working tree is shared by concurrent Claude sessions; "
        "`git add -A/.` and `git commit -a` stage THEIR uncommitted work onto "
        "your branch.\n"
        "  Do this instead:\n"
        "    1. git branch --show-current   (confirm you're on the right branch)\n"
        "    2. git status --short          (see what's actually yours)\n"
        "    3. git add <explicit paths>    (stage only your files)\n"
        "  If you truly intend the broad form, ask the human to run it via "
        "`! <command>`.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())

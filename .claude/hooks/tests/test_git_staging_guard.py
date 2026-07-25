"""Block/allow corpus for git-staging-guard.py.

Run after ANY edit to the guard:
`uv run python .claude/hooks/tests/test_git_staging_guard.py`

The first BLOCK case is the real command from 2026-07-25 whose `git add`
failed on a pathspec `git rm` had already staged — the chained commit ran
anyway and shipped a commit that deleted rr.png without adding its
replacement. The ALLOW cases are the shapes this session actually used; a
widening that breaks one of them makes the guard cost more than it saves.
"""
import json
import subprocess
import sys
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "git-staging-guard.py")


def run(command):
    payload = {"tool_name": "Bash", "hook_event_name": "PreToolUse",
               "tool_input": {"command": command}}
    done = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                          capture_output=True, text=True)
    return "BLOCK" if done.returncode == 2 else "allow"


BLOCK = [
    # The real one: add, then unrelated output, then commit — all on `;`
    'git add a.webp b.webp c.png\necho "=== staged:"\ngit commit -F msg.txt -q',
    'git add tests/x.py; git commit -m ok',
    # Still over-broad staging (the guard's original job)
    'git add -A',
    'git add .',
    'git commit -am "wip"',
    'git add -A && git commit -m "x"',
]

ALLOW = [
    # && makes a failed add fatal — the sanctioned form
    'git add tests/conftest.py tests/source_scan.py && git commit -F msg.txt -q',
    'git add a.py && git diff --cached --name-only && git commit -F m.txt -q',
    # Commit with no add in the same call
    'git commit -F msg.txt -q',
    'git status --porcelain; git log --oneline -3',
    # A heredoc commit message that TALKS about git add
    ("cat > msg.txt <<'EOF'\nfix: explain why git add needs explicit paths\n"
     "the old text said git add -A which is wrong\nEOF\n"
     "git add x.py && git commit -F msg.txt -q"),
    # Quoted mention, not a command
    'echo "run git add then git commit" > notes.txt',
    # Explicit paths, no chaining problem
    'git add src/one.py src/two.py',
]


def main():
    failures = []
    for case in BLOCK:
        if run(case) != "BLOCK":
            failures.append(f"should BLOCK but allowed: {case!r}")
    for case in ALLOW:
        if run(case) != "allow":
            failures.append(f"should ALLOW but blocked: {case!r}")
    for line in failures:
        print(line)
    print(f"{len(BLOCK)} block cases, {len(ALLOW)} allow cases, "
          f"{len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

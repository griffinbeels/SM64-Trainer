"""Block/allow corpus for no-app-server.py.

Run after ANY edit to the guard:
`uv run python .claude/hooks/tests/test_no_app_server.py`

The first BLOCK case is the real form a subagent ran on 2026-07-26 that left
two orphaned processes holding the recorder lock for hours. The ALLOW cases are
shapes this project actually uses — the fixture/contact-sheet tooling, the test
suite, and reading about the rule. A widening that breaks one of those makes the
guard cost more than it saves, which is how a guard gets disabled.
"""
import json
import subprocess
import sys
from pathlib import Path

HOOK = str(Path(__file__).resolve().parents[1] / "no-app-server.py")


def run(command):
    result = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True, text=True)
    return result.returncode, result.stderr


BLOCK = [
    # The 2026-07-26 violation, and the wrappers it could have worn.
    "uv run python -m sm64_events.main",
    "python -m sm64_events.main",
    ".venv/Scripts/python.exe -m sm64_events.main",
    "cd /d/repo && uv run python -m sm64_events.main &",
    # The desktop shell starts the same server in-process, so it takes the same
    # locks; a different spelling must not reach the same bug.
    "uv run python gui_entry.py",
    r"C:\repo\.venv\Scripts\pythonw.exe gui_entry.py",
]

ALLOW = [
    # The sanctioned way to render UI: a throwaway static server over fixtures.
    "uv run python tools/hat_sheet.py",
    "uv run python -m http.server 8791",
    # The suite imports the app via TestClient; that takes no locks.
    "uv run pytest -q",
    "uv run pytest -q tests/test_ui_caps.py",
    # Reading about the rule must never be blocked — otherwise the guard
    # obstructs its own documentation.
    'grep -rn "sm64_events.main" docs/',
    'rg -n "python -m sm64_events.main" .claude/rules/',
    'echo "never run python -m sm64_events.main"',
    # Explicit user request, opted out.
    "uv run python -m sm64_events.main  # sm64-server-ok",
    # Unrelated module runs.
    "uv run python -m sm64_events.tools.verify_addresses",
    "uv run python tools/build_hat_assets.py",
]


def main():
    failures = []
    for command in BLOCK:
        code, err = run(command)
        if code != 2:
            failures.append(f"SHOULD BLOCK but exit={code}: {command}")
        elif "Blocked:" not in err:
            failures.append(f"blocked without a reason on stderr: {command}")
    for command in ALLOW:
        code, _ = run(command)
        if code != 0:
            failures.append(f"SHOULD ALLOW but exit={code}: {command}")
    # Fail-open: malformed payloads must never brick the shell.
    result = subprocess.run([sys.executable, HOOK], input="not json",
                            capture_output=True, text=True)
    if result.returncode != 0:
        failures.append(f"must fail OPEN on bad stdin, got exit={result.returncode}")

    for line in failures:
        print(line)
    print(f"{len(BLOCK)} block + {len(ALLOW)} allow + 1 fail-open: "
          f"{'FAILED' if failures else 'all pass'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

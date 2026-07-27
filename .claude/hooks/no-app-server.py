#!/usr/bin/env python3
"""PreToolUse(Bash|PowerShell) guard — never launch the real app server.

WHY THIS EXISTS (the evidence, so a future editor doesn't "simplify" it away):
Starting `python -m sm64_events.main` takes TWO locks the user needs: the
per-db instance lock (storage/instance_lock.py) and the machine-wide RECORDER
lock (core/recorder_lock.py). While Claude holds those, the user's own running
instance silently degrades to broadcast-only and cannot record — during a live
practice session, that is their recording gone.

On 2026-07-26 a UI subagent started it anyway, twice, from a worktree, and both
processes were still alive hours later (PIDs 61904/63452, `CreationDate
7/26/2026 7:26:16 PM`). Nothing was listening on 8064/8065 so a port check would
not have found them. They surfaced only because `git worktree remove` failed at
wrap time and `rm -rf` reported `Device or resource busy` on
`data/tracker.lock` and `data/tracker.db-wal`.

The ban was already written in prose, in every one of eight subagent dispatches
("Do NOT start the app server (`python -m sm64_events.main`) — it seizes a
hardware lock the user needs"), and in CLAUDE.md's dev-process rules. Prose in a
prompt is advisory: the model weighed it against "I need a real server to render
against" and chose the server. A hook is not advisory, which is the whole point
of routing a mechanical rule here instead of writing the sentence a ninth time.

The legitimate need behind the violation is real — UI work needs a served page.
The answer is a fixture server over captured JSON (see `.claude/rules/ui.md`'s
verification norms and `tools/hat_sheet.py`, which does exactly this and cleans
up after itself), never the app.

CONTRACT
- stdin: the PreToolUse JSON payload ({"tool_input": {"command": "..."}, ...}).
- exit 0  -> allow (everything that is not an app-server launch).
- exit 2  -> block; stderr is shown to the model so it can self-correct.
- Any parse/unknown failure -> exit 0. A guard must never brick the shell;
  failing open costs the status quo, nothing more.

OPT-OUT: put `sm64-server-ok` anywhere in the command. The user asking for the
app to be launched is a legitimate case; an agent deciding on its own is not.

FALSIFIABILITY: if this blocks something legitimate, the matched text is
printed — narrow the pattern. If it ever MISSES a launch that leaves a stray
process, add the form to LAUNCH_FORMS and a case to
tests/test_no_app_server.py.
"""
import json
import re
import sys

OPT_OUT = "sm64-server-ok"

LAUNCH_FORMS = (
    # `python -m sm64_events.main`, and every wrapper of it: `uv run python -m`,
    # `.venv/Scripts/python.exe -m`, `poetry run python -m`. Keying on the `-m`
    # module form rather than on the word "python" is what makes the wrapper
    # spelling irrelevant.
    re.compile(r"-m\s+sm64_events\.main\b"),
    # The desktop shell starts the same server in-process, so it takes the same
    # two locks; blocking only the module form would leave the identical bug
    # reachable by a different spelling.
    re.compile(r"\bpython\w*(?:\.exe)?[^|;&\n]*\bgui_entry\.py\b"),
)


def launch_attempt(command: str) -> str | None:
    """The matched launch text, or None. Read-only mentions are not launches:
    a grep for the module name is how someone FINDS this rule, and blocking it
    would make the guard actively obstruct its own documentation."""
    if OPT_OUT in command:
        return None
    readers = re.compile(r"\b(grep|rg|findstr|Select-String|cat|type|head|tail|"
                         r"sed|awk|echo|Write-Output|less|more)\b", re.I)
    for form in LAUNCH_FORMS:
        found = form.search(command)
        if not found:
            continue
        # Only treat it as a launch when no read-only tool precedes the match on
        # the same command; `grep -n "sm64_events.main" docs/` is not a launch.
        if readers.search(command[:found.start()]):
            continue
        return found.group(0)
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        command = str(payload.get("tool_input", {}).get("command", ""))
    except Exception:
        return 0
    matched = launch_attempt(command)
    if not matched:
        return 0
    sys.stderr.write(
        f"Blocked: this launches the real app server (matched {matched!r}).\n\n"
        "`sm64_events.main` takes the per-db instance lock AND the machine-wide\n"
        "RECORDER lock. While you hold them the user's own instance drops to\n"
        "broadcast-only and cannot record — mid-session that is their recording\n"
        "gone. On 2026-07-26 two such processes were left running for hours and\n"
        "were invisible to a port check.\n\n"
        "For UI work, serve captured API JSON plus /ui/* from a throwaway static\n"
        "server instead — `tools/hat_sheet.py` is a working example that also\n"
        "tears itself down. See .claude/rules/ui.md's verification norms.\n\n"
        f"If the user explicitly asked you to launch the app, put {OPT_OUT!r} in\n"
        "the command.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())

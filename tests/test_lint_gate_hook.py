"""Regression tests for .claude/hooks/lint-gate.py — the agent-maintainability gate.

WHY THIS FILE EXISTS: this guard has two ways to be silently useless, and both
happened while it was being built (2026-08-28).

  1. **It fails open by design**, so a broken gate and a clean commit look
     identical from the outside. The JavaScript half was dead on arrival —
     Windows CreateProcess appends `.exe` to a bare launcher name but not
     `.cmd`, so `uvx` resolved and `npx` raised OSError, which the fail-open
     path swallowed into exit 0. Nothing reported it. `test_javascript_half_is_alive`
     is the specific pin for that.
  2. **Diff-scoping is the whole design.** If it ever degrades to linting whole
     files, every commit touching a legacy file blocks on inherited findings,
     and the correct response would be to delete the hook.
     `test_existing_findings_do_not_block` is the pin for that.

Each test builds a throwaway git repo and copies THIS repo's real rule sets
into it, so the assertions run against the shipped configuration rather than a
restatement of it.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "hooks" / "lint-gate.py"

pytestmark = pytest.mark.skipif(
    shutil.which("uvx") is None, reason="uvx not on PATH — ruff cannot run"
)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repo carrying this project's real lint configuration."""
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q")
    git(work, "config", "user.email", "gate@test")
    git(work, "config", "user.name", "gate")
    shutil.copy(REPO / "pyproject.toml", work / "pyproject.toml")
    shutil.copy(REPO / "eslint.config.mjs", work / "eslint.config.mjs")
    (work / "tools").mkdir()
    (work / "src" / "sm64_events" / "ui").mkdir(parents=True)
    git(work, "add", "pyproject.toml", "eslint.config.mjs")
    git(work, "commit", "-qm", "seed")
    return work


def fire(repo: Path, command: str = "git commit -m msg") -> subprocess.CompletedProcess:
    payload = {"tool_name": "Bash", "hook_event_name": "PreToolUse",
               "cwd": str(repo), "tool_input": {"command": command}}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True)


CLEAN_PY = "def add(left, right):\n    return left + right\n"
# Loses the exception AND the traceback: the family with direct evidence behind
# it (see the hook's docstring).
SWALLOWING_PY = ("def read(path):\n"
                 "    try:\n"
                 "        return open(path).read()\n"
                 "    except Exception:\n"
                 "        pass\n")


# --------------------------------------------------------------- routing ----

@pytest.mark.parametrize("command", [
    "git status --short",
    "git diff --cached --name-only",
    "git add tools/x.py",
    "echo 'git commit -m x'",           # the words, quoted, are not a commit
    "git commit -m msg  # lint-gate-ok",  # the deliberate escape
])
def test_allows_non_commits_and_the_escape(repo: Path, command: str):
    (repo / "tools" / "bad.py").write_text(SWALLOWING_PY, encoding="utf-8")
    git(repo, "add", "tools/bad.py")
    assert fire(repo, command).returncode == 0


def test_blocks_a_commit_that_adds_a_swallowed_error(repo: Path):
    (repo / "tools" / "bad.py").write_text(SWALLOWING_PY, encoding="utf-8")
    git(repo, "add", "tools/bad.py")
    done = fire(repo)
    assert done.returncode == 2
    assert "BLE001" in done.stderr or "S110" in done.stderr


def test_allows_a_commit_that_adds_clean_code(repo: Path):
    (repo / "tools" / "good.py").write_text(CLEAN_PY, encoding="utf-8")
    git(repo, "add", "tools/good.py")
    assert fire(repo).returncode == 0


# ---------------------------------------------------------- diff scoping ----

def test_existing_findings_do_not_block(repo: Path):
    """The design pin: a legacy file's own findings are not this commit's problem.

    Without this, the ~312-finding backlog measured on 2026-08-28 would block
    every commit touching any of those files, which is how a lint gate earns
    being deleted in its first week.
    """
    target = repo / "tools" / "legacy.py"
    target.write_text(SWALLOWING_PY, encoding="utf-8")
    git(repo, "add", "tools/legacy.py")
    subprocess.run(["git", "commit", "-qm", "legacy"], cwd=repo, check=True)

    target.write_text(SWALLOWING_PY + "\n\ndef added(value):\n    return value\n",
                      encoding="utf-8")
    git(repo, "add", "tools/legacy.py")
    done = fire(repo)
    assert done.returncode == 0, done.stderr


def test_a_new_finding_in_a_legacy_file_still_blocks(repo: Path):
    """The other half — scoping must not turn into a blanket exemption."""
    target = repo / "tools" / "legacy.py"
    target.write_text(SWALLOWING_PY, encoding="utf-8")
    git(repo, "add", "tools/legacy.py")
    subprocess.run(["git", "commit", "-qm", "legacy"], cwd=repo, check=True)

    target.write_text(SWALLOWING_PY + "\n\n" + SWALLOWING_PY.replace("read", "read2"),
                      encoding="utf-8")
    git(repo, "add", "tools/legacy.py")
    assert fire(repo).returncode == 2


def test_deleting_a_bad_line_does_not_block(repo: Path):
    """A pure deletion has no added lines, so there is nothing to lint."""
    target = repo / "tools" / "legacy.py"
    target.write_text(CLEAN_PY + "\n" + SWALLOWING_PY, encoding="utf-8")
    git(repo, "add", "tools/legacy.py")
    subprocess.run(["git", "commit", "-qm", "legacy"], cwd=repo, check=True)

    target.write_text(CLEAN_PY, encoding="utf-8")
    git(repo, "add", "tools/legacy.py")
    assert fire(repo).returncode == 0


# ------------------------------------------- the chained add+commit hole ----

def test_blocks_an_add_chained_ahead_of_the_commit(repo: Path):
    """PreToolUse fires BEFORE the command, so the index is not the answer.

    Measured hole, 2026-08-28: with `git add x && git commit -m msg` — the form
    git-staging-guard.py actively steers toward — the gate read an empty index
    and allowed a file tripping three rules. It had looked healthy for an hour.
    """
    (repo / "tools" / "bad.py").write_text(SWALLOWING_PY, encoding="utf-8")
    # Deliberately NOT staged: the add is still in the command.
    done = fire(repo, "git add tools/bad.py && git commit -m msg")
    assert done.returncode == 2, done.stderr
    assert "BLE001" in done.stderr or "S110" in done.stderr


def test_chained_add_of_clean_code_is_allowed(repo: Path):
    (repo / "tools" / "good.py").write_text(CLEAN_PY, encoding="utf-8")
    assert fire(repo, "git add tools/good.py && git commit -m msg").returncode == 0


def test_blocks_a_modified_tracked_file_added_in_the_same_command(repo: Path):
    """The added path already exists in HEAD, so it comes from the diff, not ls-files."""
    target = repo / "tools" / "legacy.py"
    target.write_text(CLEAN_PY, encoding="utf-8")
    git(repo, "add", "tools/legacy.py")
    subprocess.run(["git", "commit", "-qm", "legacy"], cwd=repo, check=True)

    target.write_text(CLEAN_PY + "\n" + SWALLOWING_PY, encoding="utf-8")
    done = fire(repo, "git add tools/legacy.py && git commit -m msg")
    assert done.returncode == 2, done.stderr


def test_commit_dash_a_sees_unstaged_modifications(repo: Path):
    """`commit -a` stages every tracked modification; the gate must see them.

    git-staging-guard.py blocks this form in this repo, but the two guards are
    independent and this one must not depend on the other still existing.
    """
    target = repo / "tools" / "legacy.py"
    target.write_text(CLEAN_PY, encoding="utf-8")
    git(repo, "add", "tools/legacy.py")
    subprocess.run(["git", "commit", "-qm", "legacy"], cwd=repo, check=True)

    target.write_text(CLEAN_PY + "\n" + SWALLOWING_PY, encoding="utf-8")
    assert fire(repo, "git commit -am msg").returncode == 2


# ------------------------------------------------------------ both halves ----

def eslint_is_available() -> bool:
    """Can eslint run here AT ALL — asked independently of the gate.

    This distinction is the entire value of the test below. The gate fails open,
    so "eslint is genuinely missing" and "the gate cannot launch eslint" both
    present as a silent pass. Deciding between them from the gate's own output
    is what let the launcher bug survive: the first version of this test skipped
    on exactly the signature it was written to catch, and reported green under a
    mutation that disabled the JavaScript half completely.
    """
    npx = shutil.which("npx")
    if npx is None:
        return False
    try:
        done = subprocess.run([npx, "--yes", "--offline", "eslint@9", "--version"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


@pytest.mark.skipif(not eslint_is_available(),
                    reason="eslint not in the npx cache; run tools/lint_changed.py --warm")
def test_javascript_half_is_alive(repo: Path):
    """The pin for the launcher-resolution bug that made the JS half a no-op.

    Windows CreateProcess appends `.exe` to a bare launcher name but not `.cmd`,
    so `uvx` resolved and `npx` raised — and fail-open turned that into exit 0.
    Because the skip above is decided WITHOUT consulting the gate, a gate that
    cannot launch eslint fails here instead of skipping.
    """
    bad = "export function pick(a, b) {\n  if (a == undefined) return b;\n  return a;\n}\n"
    (repo / "src" / "sm64_events" / "ui" / "probe.js").write_text(bad, encoding="utf-8")
    git(repo, "add", "src/sm64_events/ui/probe.js")
    done = fire(repo)
    assert done.returncode == 2, "eslint runs here, so the gate must have used it"
    assert "eqeqeq" in done.stderr


def test_test_files_keep_the_silent_failure_rules(repo: Path):
    """His call: test STYLE is unpoliced, agent-maintainability is not."""
    (repo / "tests").mkdir()
    (repo / "tests" / "test_probe.py").write_text(
        "def test_thing():\n"
        "    assert 1 == 1  # style: deliberately not policed\n"
        + SWALLOWING_PY, encoding="utf-8")
    git(repo, "add", "tests/test_probe.py")
    done = fire(repo)
    assert done.returncode == 2
    assert "S101" not in done.stderr           # style stays quiet
    assert "BLE001" in done.stderr or "S110" in done.stderr


# -------------------------------------------------------------- fail open ----

@pytest.mark.parametrize("payload", ["", "not json", "{}", '{"tool_input":{}}'])
def test_never_bricks_the_bash_tool(payload: str):
    done = subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True)
    assert done.returncode == 0


def test_outside_a_repo_is_allowed(tmp_path: Path):
    payload = {"cwd": str(tmp_path), "tool_input": {"command": "git commit -m x"}}
    done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True)
    assert done.returncode == 0

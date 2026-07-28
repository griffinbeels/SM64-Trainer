# tests/test_agent_config_parity.py
"""Two harnesses, one set of rules and one set of hooks.

Claude Code reads `CLAUDE.md` + `.claude/`; Codex reads `AGENTS.md` +
`.codex/` + `.agents/`. From 2026-07-24 to 2026-07-28 the second was a
hand-synced COPY of the first, and it came apart in every direction at once:

* `AGENTS.md` lost every dev-process rule CLAUDE.md gained in those four days
  — render-verify for UI work, exit-code honesty, the one-door law,
  star↔segment parity, the route-step-order contract — plus domain rules 11
  and 12. A Codex session was working to a four-day-old standard.
* `.agents/skills/sm64-uiux/SKILL.md` drifted the OTHER way: *it* held two
  rules (indent every nesting level; never show a state you are about to
  correct) that the `.claude/` original had lost — while `.claude/rules/ui.md`
  pointed readers at it as the copy. Which one is stale was not knowable from
  inside either.
* Worst, and the reason this is a test rather than a note: `.codex/hooks.json`
  never gained `no-app-server.py`. That guard exists because an agent starting
  `python -m sm64_events.main` takes the machine-wide RECORDER lock, and a
  human mid-practice-session loses their recording. Claude was blocked from
  2026-07-26. Codex was not.

So the rule is: **the second harness may hold pointers and its own interface
metadata, never a second copy of anything.** These checks fail when a copy
starts growing back.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CLAUDE_SETTINGS = REPO / ".claude" / "settings.json"
CODEX_HOOKS = REPO / ".codex" / "hooks.json"

# A pointer file is a frontmatter block plus a short "read the real one" note.
# Generous enough for a description and the incident it exists for; far below
# any real skill.
MAX_POINTER_CHARS = 2_500

# AGENTS.md is a router: read CLAUDE.md, plus the zone table Codex has to walk
# by hand because it cannot auto-load a `paths:`-scoped rule. Not a rulebook.
MAX_AGENTS_MD_CHARS = 6_000

_ABSOLUTE_USER_PATH = re.compile(r"(?:[A-Za-z]:[\\/]+|/)(?:Users|home)[\\/]+", re.I)


def hook_scripts(config: dict) -> set[str]:
    """Every hook script a harness config runs, as repo-relative posix paths."""
    found = set()
    for entries in config.get("hooks", {}).values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                for match in re.findall(r"[\w./\\$${}-]+\.py", hook.get("command", "")):
                    cleaned = match.replace("\\", "/").replace("$CLAUDE_PROJECT_DIR/", "")
                    cleaned = cleaned.replace("${CLAUDE_PROJECT_DIR}/", "")
                    # removeprefix, never lstrip: lstrip("./") eats the leading
                    # dot of ".claude" too, and the probe below is what caught
                    # that — the guard "passed" against a path set that could
                    # not match anything real.
                    found.add(cleaned.removeprefix("./"))
    return found


def agent_skill_pointers() -> list[Path]:
    return sorted((REPO / ".agents" / "skills").glob("*/SKILL.md"))


def test_both_harnesses_run_the_same_hook_scripts():
    claude = hook_scripts(json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8")))
    codex = hook_scripts(json.loads(CODEX_HOOKS.read_text(encoding="utf-8")))
    # SessionStart cleanup is a Claude-Code-only lifecycle event; the GUARDS
    # (PreToolUse/PostToolUse) are what must match, and dev_cleanup is the one
    # entry that is not one.
    guards_claude = {p for p in claude if "dev_cleanup" not in p}
    assert guards_claude == codex, (
        "the two harnesses run different guard hooks.\n"
        f"  only Claude Code runs: {sorted(guards_claude - codex)}\n"
        f"  only Codex runs:       {sorted(codex - guards_claude)}\n"
        "Every guard here exists because prose already failed to stop the "
        "thing once. A guard that binds one harness and not the other is a "
        "guard that binds nobody — no-app-server.py was in exactly that state "
        "for two days while it was the only thing standing between an agent "
        "and the user's live recording.")


def test_codex_runs_the_shared_hook_scripts_not_its_own_copies():
    codex = hook_scripts(json.loads(CODEX_HOOKS.read_text(encoding="utf-8")))
    assert codex, "Codex runs no hooks at all"
    own = [p for p in codex if not p.startswith(".claude/hooks/")]
    assert not own, (
        f"Codex points at its own hook copies: {own}. Point at "
        "`.claude/hooks/*.py` instead — two copies of a guard means one of "
        "them is the stale one and nothing says which.")
    assert not (REPO / ".codex" / "hooks").exists(), (
        ".codex/hooks/ is back. It was deleted 2026-07-28 because its copies "
        "had already drifted from .claude/hooks/.")


@pytest.mark.parametrize(
    "path", [CODEX_HOOKS, *agent_skill_pointers(), REPO / "AGENTS.md"],
    ids=lambda p: p.relative_to(REPO).as_posix())
def test_no_agent_config_hardcodes_a_users_home_directory(path):
    """`C:\\Users\\<name>\\...` works in exactly one checkout on one machine."""
    offending = [line for line in path.read_text(encoding="utf-8").splitlines()
                 if _ABSOLUTE_USER_PATH.search(line)
                 and "C:\\\\Users\\\\..." not in line]   # the note explaining this rule
    assert not offending, (
        f"{path.relative_to(REPO).as_posix()} hardcodes an absolute user path: "
        f"{offending}. Use a repo-relative path (Codex) or "
        "$CLAUDE_PROJECT_DIR (Claude Code).")


@pytest.mark.parametrize("path", agent_skill_pointers(),
                         ids=lambda p: p.parent.name)
def test_agent_skills_are_pointers_not_copies(path):
    text = path.read_text(encoding="utf-8")
    canonical = REPO / ".claude" / "skills" / path.parent.name / "SKILL.md"
    if not canonical.exists():
        pytest.skip(f"{path.parent.name} has no .claude counterpart — it is "
                    "genuinely Codex-only (Codex has no user-level skills dir)")
    assert len(text) <= MAX_POINTER_CHARS, (
        f"{path.relative_to(REPO).as_posix()} is {len(text):,} chars — it has "
        "grown back into a copy of "
        f"{canonical.relative_to(REPO).as_posix()}. Replace the body with a "
        "pointer to that file. The two copies of this exact skill drifted in "
        "OPPOSITE directions inside four days.")
    assert canonical.relative_to(REPO).as_posix() in text, (
        f"{path.relative_to(REPO).as_posix()} does not name its canonical "
        f"file ({canonical.relative_to(REPO).as_posix()}), so a reader landing "
        "here has no way to find the real text.")


def test_agents_md_routes_to_claude_md_and_holds_no_rules_of_its_own():
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "CLAUDE.md" in text, (
        "AGENTS.md must send the reader to CLAUDE.md — it is the guide.")
    assert len(text) <= MAX_AGENTS_MD_CHARS, (
        f"AGENTS.md is {len(text):,} chars (ceiling {MAX_AGENTS_MD_CHARS:,}). "
        "It is a router, not a rulebook: anything longer means the module map "
        "or the domain rules are being duplicated here again, which is how it "
        "silently fell four days behind CLAUDE.md in July 2026. Put the rule "
        "in CLAUDE.md or a .claude/rules/ file and link it.")


def test_agents_md_names_every_rule_file_codex_must_open_by_hand():
    """Codex cannot auto-load a `paths:`-scoped rule; the table is the manual
    version of that mechanism, so a rule missing from it reaches Codex never."""
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    missing = [p.name for p in sorted((REPO / ".claude" / "rules").glob("*.md"))
               if p.name not in text]
    assert not missing, (
        f"AGENTS.md's zone table does not name {missing}. Claude Code injects "
        "these on a file read; Codex has to be told to open them.")


def test_the_guards_can_still_fail(tmp_path):
    """Probed in both directions (tests/source_scan.py's rule)."""
    same = {"hooks": {"PreToolUse": [{"hooks": [
        {"command": 'python "$CLAUDE_PROJECT_DIR/.claude/hooks/a.py"'}]}]}}
    other = {"hooks": {"PreToolUse": [{"hooks": [
        {"command": "python .claude/hooks/a.py"}]}]}}
    assert hook_scripts(same) == hook_scripts(other) == {".claude/hooks/a.py"}

    dropped = {"hooks": {"PreToolUse": [{"hooks": [
        {"command": "python .claude/hooks/a.py"},
        {"command": "python .claude/hooks/b.py"}]}]}}
    assert hook_scripts(dropped) != hook_scripts(other)

    assert _ABSOLUTE_USER_PATH.search(r"python 'C:\Users\someone\repo\x.py'")
    assert _ABSOLUTE_USER_PATH.search("python /Users/someone/repo/x.py")
    assert not _ABSOLUTE_USER_PATH.search("python .claude/hooks/x.py")

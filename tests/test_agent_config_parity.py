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

# Both readers route to one generated index; AGENTS.md contains no zone table.
MAX_AGENTS_MD_CHARS = 1_500

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


def skill_identity(path: Path) -> str:
    """Discovery uses the declared name, even when the folder was renamed."""
    text = path.read_text(encoding="utf-8")
    frontmatter = re.match(r"---\n(.*?)\n---", text, re.S)
    assert frontmatter, f"{path}: missing skill frontmatter"
    name = re.search(r"^name:\s*(.+)$", frontmatter.group(1), re.M)
    assert name, f"{path}: missing skill name"
    return name.group(1).strip().strip("\"'")


def shared_skill_names() -> set[str]:
    """Every skill the shared harness owns, which no project skill may shadow.

    Without the harness on disk this used to silently narrow to one known
    name, so a machine missing the harness read as a PASS on a guard that had
    seen almost nothing. Skip instead: the guard cannot check what it cannot
    see, and it must say so."""
    shared = Path.home() / ".claude" / "harness" / "skills"
    if not shared.is_dir():
        pytest.skip(f"the shared harness is not installed at {shared}; this "
                    "guard cannot see the identities it protects")
    names = {"create-artifacts"}   # the known regression, named explicitly
    names.update(skill_identity(path) for path in shared.glob("*/SKILL.md"))
    return names


def test_project_skills_do_not_shadow_shared_identities():
    shared = shared_skill_names()
    local = [*agent_skill_pointers(),
             *(REPO / ".claude" / "skills").glob("*/SKILL.md")]
    duplicates = [str(path.relative_to(REPO)) for path in local
                  if skill_identity(path) in shared]
    assert not duplicates, (
        f"Project skills shadow the shared harness: {duplicates}. Remove the "
        "local workflow; project facts belong in the project guide or zone rules.")


def test_both_harnesses_run_the_same_hook_scripts():
    claude = hook_scripts(json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8")))
    codex = hook_scripts(json.loads(CODEX_HOOKS.read_text(encoding="utf-8")))
    # Every row, including the SessionStart cleanup: Codex has the same
    # lifecycle events (its hooks doc, fetched 2026-09-04), and the harness
    # generator carries every Claude row across, translating matchers only.
    # Until 2026-09-04 this test exempted dev_cleanup on the belief that
    # SessionStart was Claude-only; it is not.
    assert claude == codex, (
        "the two harnesses run different hook scripts.\n"
        f"  only Claude Code runs: {sorted(claude - codex)}\n"
        f"  only Codex runs:       {sorted(codex - claude)}\n"
        "Every guard here exists because prose already failed to stop the "
        "thing once. A guard that binds one harness and not the other is a "
        "guard that binds nobody — no-app-server.py was in exactly that state "
        "for two days while it was the only thing standing between an agent "
        "and the user's live recording.")


def test_codex_runs_the_shared_hook_scripts_not_its_own_copies():
    codex = hook_scripts(json.loads(CODEX_HOOKS.read_text(encoding="utf-8")))
    assert codex, "Codex runs no hooks at all"
    own = [p for p in codex if p.startswith(".codex/")]
    assert not own, (
        f"Codex points at its own hook copies: {own}. Point at "
        "`.claude/hooks/*.py` (or a shared tool such as tools/dev_cleanup.py) "
        "instead — two copies of a guard means one of them is the stale one "
        "and nothing says which.")
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
    assert canonical.exists(), (
        f"{path.parent.name} has no shared project body under .claude/skills. "
        "Codex also discovers user-level skills; missing counterparts do not "
        "exempt a local copy from parity.")
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


@pytest.mark.parametrize("entry", ["AGENTS.md", "CLAUDE.md"])
def test_both_readers_use_the_canonical_rule_index(entry):
    text = (REPO / entry).read_text(encoding="utf-8")
    assert "[" in text and "](docs/rule-index.md)" in text
    assert (REPO / "docs/rule-index.md").is_file()
    assert not re.search(r"^\|.*\.claude/rules/.*\|$", text, re.M), (
        f"{entry} has grown its own rule table; route through docs/rule-index.md")


def test_skill_identity_detects_a_renamed_duplicate(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "REPO", tmp_path)
    skill = tmp_path / ".agents/skills/renamed/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: "create-artifacts"\n---\nbody', encoding="utf-8")
    assert skill_identity(skill) in shared_skill_names()
    with pytest.raises(AssertionError, match="shadow"):
        test_project_skills_do_not_shadow_shared_identities()
    skill.write_text('---\nname: project-specific\n---\nbody', encoding="utf-8")
    assert skill_identity(skill) not in shared_skill_names()
    test_project_skills_do_not_shadow_shared_identities()


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


HARNESS_INSTALLER = Path.home() / ".claude" / "harness" / "install.py"


def test_codex_hooks_file_is_generated():
    """`.codex/hooks.json` is GENERATED from `.claude/settings.json` by the
    harness repo's installer (2026-09-04), never hand-written -- the hand-written
    file it replaced could not be parsed by Codex for 38 days (a `_comment` key
    Codex rejects) while the name-diffing tests above stayed green, because a
    diff of two files cannot see that one reader refuses to load one of them.

    History the generated file's own description keeps, so it is not lost here
    either: `.codex/hooks/*.py` were deleted 2026-07-28 after `no-app-server.py`
    -- the guard that stops an agent seizing the recorder lock out from under a
    live practice session -- shipped to `.claude/settings.json` on 2026-07-26 and
    never reached the Codex mirror.

    The installer lives at `~/.claude/harness/install.py` on a machine that has
    the harness installed; without it this test skips, so its teeth were proved
    by mutation (edit the committed file by hand -> FAIL naming the hunk ->
    regenerate -> PASS) rather than by a red phase it cannot have.
    Regenerate with `python ~/.claude/harness/install.py --repo .`"""
    if not HARNESS_INSTALLER.exists():
        pytest.skip(f"no harness installed at {HARNESS_INSTALLER}")
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, str(HARNESS_INSTALLER), "--repo", str(REPO), "--check"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, (
        ".codex/hooks.json differs from what .claude/settings.json generates -- "
        "run `python ~/.claude/harness/install.py --repo .`:\n" + result.stdout + result.stderr)

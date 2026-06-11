---
name: create-artifacts
description: Use when wrapping up a feature, fix, or milestone — after merge, after a live audit, or when the user says "wrap up", "create artifacts", "document findings", or "make sure we learn from this". Turns the session's mistakes, review findings, and hard-won facts into artifacts placed where they actually prevent recurrence.
---

# Create Artifacts

## Overview

Every mistake gets made exactly once. This skill converts a finished work
session into artifacts — tests, hooks, comments, docs, skills, memories —
each placed where the *next* session will actually encounter it before
repeating the mistake. **Placement beats existence: an artifact nobody hits
at the decision point is a artifact that doesn't exist.**

## Step 1 — Harvest the session

Sweep the ACTUAL conversation and git history, not your recollection:

- `git log --oneline <base>..HEAD` — every `fix:` commit is a finding; read its message for the root cause
- Review verdicts (Critical/Important findings, including ones that turned out to be plan/spec flaws)
- User corrections and repeated feedback ("twice asked for X" = a preference)
- Implementer concerns / DONE_WITH_CONCERNS flags (esp. spec-vs-tree drift)
- Assumptions shipped unverified (VERIFY markers added this session)
- Patterns that WORKED non-obviously (promote, not just prevent)
- Claims disproven this session (note the exact old phrasing — needed in Step 3)

## Step 2 — Route each finding (the core table)

Work the table top-down; the FIRST matching row wins. Prose is the weakest
artifact — always check the rows above it first.

| Finding is... | Artifact | Where |
|---|---|---|
| A behavior that regressed or nearly did | **Pinning/regression test** (strongest artifact) | The module's test file; name it after the failure |
| A rule a machine can check (forbidden pattern, required pairing) | **Hook / lint / CI check** | hookify rule or test-suite guard — don't write prose for mechanical rules |
| A constraint invisible in the code at one spot | **Comment/docstring AT that line** | Where the next editor's cursor will be |
| A module-local contract or rationale | **Module docstring** | The module |
| Cross-cutting domain knowledge + its evidence | **Project knowledge doc** | e.g. docs/architecture.md — record evidence WITH the fact |
| Consumer-facing surface change | **API docs / README** | The consumer contract file |
| A workflow/process lesson (how Claude should work) | **Skill update — user level** | Edit the skill that was IN USE when the mistake happened (e.g., a plan-execution flaw → the plan-writing/executing skill). Never a new grab-bag "lessons" skill |
| A user preference or project reality not in the repo | **Memory** | The persistent memory dir + MEMORY.md index — NOT project docs |
| An unproven assumption | **VERIFY marker with a falsifiability test** | At the assumption: "run X, observe Y → holds; observe Z → wrong, consequence is W" |

One fact, one home; everywhere else links. For always-loaded files
(CLAUDE.md), apply a lean budget — pointer lines, not paragraphs.

## Step 3 — Kill stale twins

For every claim corrected this session, grep the OLD phrasing repo-wide —
README, docs, docstrings, comments, AND other branches/worktrees
(`git grep "<phrase>" $(git branch --format='%(refname:short)')`). A stale
copy on an unmerged branch WILL merge back; fix it or flag it to that
branch's owner. "Not my branch" is not a resolution.

## Step 4 — The placement test

For each artifact ask: *"When someone is about to repeat this mistake,
where are they standing — and is this artifact in their line of sight
there?"* If the answer is "they'd have to go read a lessons file", re-route
it (usually to a test, hook, or point-of-use comment).

## Step 5 — Verify and commit

Run the full test suite (new pinning tests must pass; nothing broken).
Commit with messages that explain WHY. Update memory index if memories were
written.

## Red Flags — stop and re-route

| Rationalization | Reality |
|---|---|
| "A doc rule covers it" | If a test or hook can enforce it, prose is the weakest option. Route up the table. |
| "I'll write one lessons/retro file" | Centralized lessons are read by nobody at the decision point. Distribute to homes. |
| "That stale copy is on another branch" | It will merge. Fix it or flag the owner — silence ships the bug twice. |
| "The user preference is noted in the docs" | Preferences describe the USER, not the code. They belong in memory. |
| "This lesson is project-specific" | Workflow lessons (how to plan, review, execute) are not — route to user-level skills. |
| "VERIFY: unconfirmed" | Incomplete. Every VERIFY needs the exact test, the observable, and the consequence. |
| "No bugs this session, nothing to write" | Harvest also promotes patterns that worked and assumptions that shipped. |
| "It fits in two homes, I'll add it to both" | Run the placement test; ONE home wins. The other gets a link only if it serves a different audience — never a second copy of the content. |

<!-- Mirror: canonical copy lives at ~/.claude/skills/create-artifacts/SKILL.md
     on the development machine. Keep in lockstep. -->

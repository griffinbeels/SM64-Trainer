---
name: create-artifacts
description: Use when wrapping up a feature, fix, or milestone — after merge, after a live audit, or when the user says "wrap up", "create artifacts", "document findings", or "make sure we learn from this". Turns the session's mistakes, review findings, and hard-won facts into artifacts placed where they actually prevent recurrence.
---

# Create Artifacts

## Overview

Every mistake gets made exactly once. This skill converts a finished work
session into artifacts — tests, hooks, comments, docs, skills, memories —
each placed where the *next* session will actually encounter it before
repeating the mistake.

**Placement is necessary but not sufficient. The real test is consumption:
will a future session actually LOAD this artifact at the decision point?**
An artifact nobody hits there is an artifact that doesn't exist — and most
artifact types are only hit if something *pulls them into context*. So your
job isn't just "write the lesson down somewhere," it's "write it into a
channel that has a reader."

## What actually gets consumed (measured, don't guess)

A consumption audit of this approach across ~25 real sessions found the hit
rate is wildly uneven. Route toward the channels that have a reader:

- **Always-loaded project files** (CLAUDE.md, architecture.md) — read/grepped
  constantly; the reliable cross-session channel. Strong.
- **Point-of-use comments / docstrings** — hit whenever someone edits that
  line; they survive later edits and mark the exact spot a regression returns.
  Strong (passive).
- **Pinning tests** — the test runner hits them every session. Strong
  (passive guard) even if they rarely go red.
- **VERIFY markers** — the live gate finds and clears them; the add→gate→flip
  loop works. Strong, but watch the backlog (see Step 5).
- **Hooks** — fire on *every* matching tool call regardless of what's in
  context. The strongest enforcement for a mechanical rule — and the most
  *under-used*: audits repeatedly find recurring mechanical mistakes routed to
  weak prose because "a hook is more work." Don't. (See Step 2.)
- **Standalone prose with no reader** — a retro file, a lessons doc, or any
  artifact whose only access path is "someone decides to go read it" — is
  cold. Nearly never consumed.
- **Memory bodies** — the index line (MEMORY.md) is loaded each session, but
  whether the *body* is recalled depends on a recall mechanism being wired up;
  verify it actually surfaces in this environment before relying on body
  recall. Until then, the memory's one-line index hook is its real surface —
  make that line carry the actionable gist.

The throughline: **prefer the channel you can name a trigger for.** Step 4
makes that a hard check.

## Step 1 — Harvest the session

Sweep the ACTUAL conversation and git history, not your recollection:

- `git log --oneline <base>..HEAD` — every `fix:` commit is a finding; read its message for the root cause
- Review verdicts (Critical/Important findings, including ones that turned out to be plan/spec flaws)
- User corrections and repeated feedback ("twice asked for X" = a preference)
- **Recurring MECHANICAL mistakes** — the same wrong command/pattern that bit more than once (wrong-branch commit, over-broad staging, a forbidden API). These are hook candidates; mark them as such while harvesting.
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
| A rule a machine can check (forbidden pattern, required pairing) — *especially one that has bitten more than once* | **Hook / lint / CI check** | A `.claude/hooks/` script wired in `settings.json` — write it THIS pass, don't downgrade to prose (see below) |
| A constraint invisible in the code at one spot | **Comment/docstring AT that line** | Where the next editor's cursor will be |
| A module-local contract or rationale | **Module docstring** | The module |
| Cross-cutting domain knowledge + its evidence | **Project knowledge doc** | e.g. docs/architecture.md — record evidence WITH the fact |
| Consumer-facing surface change | **API docs / README** | The consumer contract file |
| A workflow/process lesson (how Claude should work) | **Skill update — user level** | Edit the skill that was IN USE when the mistake happened (e.g., a plan-execution flaw → the plan-writing/executing skill). Never a new grab-bag "lessons" skill |
| A user preference or project reality not in the repo | **Memory** | The persistent memory dir + MEMORY.md index — NOT project docs |
| An unproven assumption | **VERIFY marker with a falsifiability test** | At the assumption: "run X, observe Y → holds; observe Z → wrong, consequence is W" |

One fact, one home; everywhere else links. For always-loaded files
(CLAUDE.md), apply a lean budget — pointer lines, not paragraphs.

### Writing the hook (the under-used row — make it real)

A recurring mechanical mistake is the one finding type where prose provably
fails: a comment is read only if an editor happens to be at that line, a
memory only if recall fires — but a **hook fires on every matching call**.
When Step 1 surfaced a mechanical mistake that recurred, create the hook now:

- Add a small script under `.claude/hooks/` and wire it in `.claude/settings.json`
  (project-scoped) or `settings.local.json`. `PreToolUse` to block before the
  fact; `PostToolUse` to catch after.
- The script reads the tool payload as JSON on stdin. **Exit 2 to block** and
  write the reason to stderr (the model sees it and self-corrects). **Exit 0
  to allow.**
- **Fail open**: on any parse error or unknown input, exit 0. A guard must
  never brick the tool it guards — the worst case of failing open is the
  status quo without the hook.
- Make the matcher specific and strip shell/quote context so legitimate
  commands don't false-trigger; test it against a block-list AND an allow-list
  before trusting it.

If you can't reduce the rule to a machine check, *then* fall to a comment or
(for workflow rules) a skill update — but only after you've tried the hook.

## Step 3 — Kill stale twins (run it; don't claim it)

For every claim corrected this session, grep the OLD phrasing everywhere it
could survive — and treat "clean" as a result you have to SHOW, not assert.
A twin that survives on an unmerged branch WILL merge back and re-ship the bug.

Run, and read the actual output:

```
git grep -n "<old phrase>" $(git for-each-ref --format='%(refname:short)' refs/heads)
# plus any worktrees that won't show as local branches:
git worktree list   # then grep each worktree path
```

For each hit: fix it, or — if it's a sibling branch another session owns —
flag the owner. "Not my branch" is not a resolution; "I ran the grep and it
returned nothing" with the command shown IS. (A live audit found a twin
sweep that minted a master-only fix while the stale copy sat on an active
worktree, ready to regress on merge.)

## Step 4 — The placement test: name the trigger

For each artifact, answer concretely: **"What exact mechanism loads this into
a future session's context at the moment it's needed?"**

- ✅ Nameable: "the test runner runs it"; "it's in CLAUDE.md, always loaded";
  "it's a comment on the line they'll edit"; "the hook fires on that tool
  call"; "the live gate greps VERIFY".
- ❌ Not nameable: "they'd open the retro file"; "they'd browse the memory
  dir"; "it's documented" (where? loaded by what?).

If the honest answer is a maybe — "*if* they go read it", "*if* recall fires"
— the artifact is cold. Upgrade it up the table to a channel whose trigger you
can name. This is the difference between an artifact that prevents a mistake
and one that just records it.

## Step 5 — Verify and commit

Run the full test suite (new pinning tests must pass; nothing broken). If you
wrote a hook, test it against block + allow cases. Commit with messages that
explain WHY. Update the memory index if memories were written. Surface the
standing **VERIFY backlog** (`grep -rn VERIFY src/ | wc -l`) so unresolved
markers don't silently rot across sessions — note any that have been pending
more than a session.

## Red Flags — stop and re-route

| Rationalization | Reality |
|---|---|
| "A doc rule covers it" | If a test or hook can enforce it, prose is the weakest option. Route up the table. |
| "I'll note the mechanical rule in a comment/memory" | A comment is read only if an editor is at that line; a hook fires every time. If it's mechanical and recurred, write the hook (Step 2). |
| "This pass produced no test and no hook" | Smell. Re-check: was there a near-miss that deserves a pinning test, or a recurring mechanical rule that deserves a hook? A pure-prose pass routed everything to the bottom of the table. |
| "I'll write one lessons/retro file" | Centralized lessons are read by nobody at the decision point. Distribute to homes. |
| "The stale-twin sweep came back clean" | Show the command and its output. An asserted-clean sweep that wasn't actually run is how a twin survives on a sibling branch and merges back. |
| "That stale copy is on another branch" | It will merge. Fix it or flag the owner — silence ships the bug twice. |
| "The user preference is noted in the docs" | Preferences describe the USER, not the code. They belong in memory. |
| "This lesson is project-specific" | Workflow lessons (how to plan, review, execute) are not — route to user-level skills. |
| "VERIFY: unconfirmed" | Incomplete. Every VERIFY needs the exact test, the observable, and the consequence. |
| "No bugs this session, nothing to write" | Harvest also promotes patterns that worked and assumptions that shipped. |
| "It fits in two homes, I'll add it to both" | Run the placement test; ONE home wins. The other gets a link only if it serves a different audience — never a second copy of the content. |

<!-- Mirror: a copy of this skill is versioned in repos that adopt it
     (e.g. sm64_tracker/.claude/skills/create-artifacts/). Keep in lockstep. -->

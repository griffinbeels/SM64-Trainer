---
name: release
description: >-
  Build and publish a new GitHub release of SM64 Trainer — the six-asset
  incremental-update set (app zip + manifest + bootstrap installer, each with
  a .sha256) plus the in-app auto-update notes. Use this whenever the user
  wants to cut, ship, or publish a release, bump the version, or "make a new
  version / new build", even if they don't name tools/release.py. Trigger on
  "release", "cut a release", "ship it", "publish a version", "new build".
---

# Release — read the canonical skill

**The procedure lives at `.claude/skills/release/SKILL.md`. Read that file now
and follow it exactly.** It owns the version choice, the user-facing note
generation from commit history, the `tools/release.py` invocation, and the
post-publish verification.

This file is a pointer, not a copy. Publishing is the most irreversible thing
this repo does, and a duplicated procedure means that the first time the
six-asset set changes, one of the two copies is wrong — with no way to tell
which from inside a session.

`tests/test_agent_config_parity.py` fails if this file grows back into a copy.

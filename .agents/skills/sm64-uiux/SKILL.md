---
name: sm64-uiux
description: Design, audit, redesign, implement, or visually verify the SM64 Trainer interface under src/sm64_events/ui. Use for information architecture, first-use clarity, interaction flow, UX copy, visual systems, playful SM64-inspired styling, responsive behavior, accessibility, screenshot-driven frontend changes, and anti-"AI slop" polish. Preserve every existing workflow and backend contract. Do not use for backend-only changes.
---

# SM64 Trainer UI/UX — read the canonical skill

**The skill text lives at `.claude/skills/sm64-uiux/SKILL.md`. Read that file
now**, plus whichever of its references the task needs:

- `.claude/skills/sm64-uiux/references/product-principles.md`
- `.claude/skills/sm64-uiux/references/functional-inventory.md`

This file is a pointer, not a copy. It was the full text, duplicated
byte-for-byte, from 2026-07-24 until 2026-07-28 — and in those four days the
two copies came apart in the direction nobody expects: *this* one was the one
carrying two rules (indent every nesting level; never show a state you are
about to correct) that the `.claude/` original had lost. Which copy is stale is
not knowable from inside either one, which is the whole argument against having
two.

`agents/openai.yaml` beside this file is genuinely Codex-only (display name,
default prompt) and is duplicated nowhere.

`tests/test_agent_config_parity.py` fails if this file grows back into a copy.

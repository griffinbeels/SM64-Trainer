---
name: sm64-uiux
description: Design, audit, redesign, implement, or visually verify the SM64 Trainer interface under src/sm64_events/ui. Use for information architecture, first-use clarity, interaction flow, UX copy, visual systems, playful SM64-inspired styling, responsive behavior, accessibility, screenshot-driven frontend changes, and anti-"AI slop" polish. Preserve every existing workflow and backend contract. Do not use for backend-only changes.
---

# SM64 Trainer UI/UX

Make SM64 Trainer feel like a joyful practice companion: immediately understandable
to a new player, fast for an expert, visually distinctive, and faithful to every
existing capability.

## Required context

- Read [references/product-principles.md](references/product-principles.md) before
  any material visual or interaction change.
- Read [references/functional-inventory.md](references/functional-inventory.md)
  before changing navigation, the app shell, Practice, or feature placement.
- Read the relevant UI source and its tests before editing. Treat
  `tests/test_ui_section_parity.py` as a hard star/segment parity contract.
- Treat the current main-course star selector as approved visual language. Preserve
  its information, behavior, and prominence unless the user explicitly revisits it.

## Workflow

### 1. Lock the experience contract

Name the player flow being improved. Inventory every visible action, state, and
piece of feedback on the affected surface. Separate:

1. practice-now information;
2. feedback and progress;
3. optional analysis;
4. authoring and settings;
5. system administration.

Do not remove a capability merely because it creates clutter. Re-home, group, label,
or progressively disclose it.

### 2. Concept before implementation

For a nontrivial redesign, create full-surface concepts before coding. Use the
installed Imagegen skill when available and use current screenshots as structural
references.

- Keep real labels, data, controls, and workflow constraints.
- Do not invent metrics, product claims, navigation, or unrelated features.
- When direction is unsettled, produce exactly three genuinely different but
  comparable concepts. Vary hierarchy, spatial model, and art direction—not only
  color.
- Include enough of the screen to judge the complete primary workflow.
- Pause for the user's design-direction approval before implementation.

Once approved, treat the concept as the visual specification. Record any intentional
deviation.

### 3. Extract a small design system

Define tokens and rules before broad edits:

- color roles and semantic states;
- display, UI, and numeric typography;
- spacing rhythm and container model;
- buttons, tabs, menus, rows, panels, and focus states;
- icon and illustration treatment;
- motion timing plus reduced-motion behavior;
- desktop and narrow-window behavior.

Follow the existing zero-build Preact/HTM architecture. Add no framework or runtime
dependency unless the user explicitly approves it.

### 4. Implement in vertical slices

Prefer this order:

1. app shell and navigation;
2. Practice first viewport and active objective;
3. progress and attempt history;
4. workshop surfaces (Segments and Routes);
5. Run, Compare, Live feed, and modals.

Keep each slice functional. Preserve API calls, WebSocket behavior, localStorage
keys, mounted-state requirements, keyboard behavior, and browser/desktop parity.

### 5. Verify the rendered product

Use the installed Browser skill and in-app browser first.

- Exercise the target flow, not just page load.
- Check the primary desktop viewport and one narrow viewport.
- Inspect focus visibility, labels, affordances, overflow, wrapping, contrast,
  reduced motion, empty/loading/offline states, and destructive-action placement.
- Compare the accepted concept and latest browser screenshot with `view_image`.
- Keep a mismatch ledger and fix every material mismatch.
- Run the relevant tests, then `uv run pytest -q` before handoff.

## Quality gates

- Make the current objective unmistakable within one glance.
- Keep primary actions visually stronger than settings and maintenance.
- Keep advanced power available without asking a beginner to parse it first.
- Use playful details to reinforce state, progress, and reward.
- Avoid nested-card sprawl, badge/pill confetti, generic dashboards, gratuitous
  gradients/glows, decorative fake metrics, and browser-default typography.
- Keep timing and dense numeric data highly scannable.
- Never communicate essential state through color or motion alone.

## Handoff

Report the approved concept, preserved workflows, rendered viewports, target flow
tested, tests run, material mismatches fixed, and remaining intentional deviations.

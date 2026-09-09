# Immediate replay review prototype

**Goal:** Shorten the path from a completed attempt to understanding and correcting its execution against a template.
**Question this answers:** Can Griffin isolate, align, and loop a trick quickly enough to fit review between attempts?
**Lane:** prototype; existing captured-picture/input identity and saved replay contracts remain binding.
**Status:** historical initial prototype scope; later feedback expanded capture/storage work and explicitly removed the visible Review latest button. Current implementation remains in `codex/replay-review`.
**Dependency verdict:** Independent. UI concept B (controls below picture) selected by Griffin: “Let’s go with the recommendation”. This approves the layout for implementation, not the experienced prototype.

**Planned touch-set:** replay controls and timeline UI, inputs template payload, replay review-state service/routes, isolated fixtures and focused tests. No changes to `main.py`, tracking projection, snapshot/events/memory, console setup, or onboarding in this prototype wave. Media preparation is a separate investigation until its integration contract is proved.

**Collision notes:** Console/setup siblings own header, server app, and main wiring. Review latest mounts through practice surfaces/app-owned state without changing their modules. The old input-timeline branch is already an ancestor of main. Do not touch sibling worktrees or their recording data.

**Initially selected behavior (historical):** one shared transport below the picture for captured/downloaded media; existing frame navigation and presented-picture clock; template-only integer-frame translation; full source template available beyond clipped attempt; zoom/Fit; explicit A/B and loop toggle; per-attempt replay-lifetime review state, promoted on save and editable after save; visible Review latest plus focused shortcut with honest focus scope. No inferred game inputs for downloaded media.

**Remaining larger experiment:** Continuous media/index/input preparation during the attempt, with authoritative resets/successes and final publication, remains required for immediate review. Existing media probes support duration-aware copy with logical bounds but do not certify final-segment delivery or browser/audio seams. This prototype must not claim that performance target is already achieved.

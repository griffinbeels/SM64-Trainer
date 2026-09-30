# Saving default segment fixes

Launch `run-test-server.bat` from the checkout containing your work. This
explicitly enables maintainer authoring. In the segment editor and recorder,
**Save for everyone** is the primary action; **Save only for me** keeps an
experiment personal. Switching editors and accepting the save prompt uses the
same primary scope. Regular source launches and released apps save personally.
Packaged apps cannot enable shared authoring, even with the maintainer flag.

A shared save writes the complete definition into the tracked authoring input
`src/sm64_events/data/segments.authored.json` and regenerates
`src/sm64_events/data/defaults.seed.json`. Existing defaults retain their stable
seed key and local database ID. New segments receive a stable seed key; an
existing personal segment can be promoted through the same save button.
Attempts, PBs, strategy selections, library links and other preferences stay local.

**Saved for the next release** means the source files and local definition are
saved. Include both files in the feature commit and next release. The app does
not push Git commits or publish releases from this button. Regeneration keeps
the authored definition, and direct builds refuse a stale generated seed.
When the base corpus changes underneath an authored correction, generation
refuses the conflict so a maintainer reviews it instead of silently hiding it.

After updating, fresh installs and existing untouched defaults receive the new
definition. Personal customizations and intentionally deleted defaults survive.
**Reset to shipped version** restores every definition field and opts a personal
copy back into future default updates. A shared save of an old personal correction
promotes it deliberately; existing database edits are never silently published.

Shared parents must be portable star or area identities. A `segment:<local id>`
parent belongs to one database, so shared save refuses it with a repair message;
save it personally until its parent can be represented portably. Hide/show,
deletion, strategy choices and split/merge drafts remain personal actions.

The implementation has three owners: `tracking/shared_defaults.py` validates
and writes the catalogue, `tools/build_defaults_seed.py` assembles base corpus
plus authored definitions, and `tracking/defaults.py` reconciles shipped data
while respecting personal copies. The generated seed is never edited by hand.

# Claude Design sync — repo notes

## The loop

Adding or changing a published component is **one row** in
`.design-sync/components.mjs`. Everything downstream is generated from it: the
bundle entry, the TypeScript contract, and the per-component doc that becomes
the design agent's `.prompt.md`. Then:

```
node .design-sync/facade/build.mjs
node .ds-sync/resync.mjs --config .design-sync/config.json \
  --node-modules .ds-sync/facade-build/node_modules \
  --entry .ds-sync/facade-build/dist/index.js --out ./ds-bundle
uv run pytest tests/test_design_sync_registry.py -q
```

Then author `.design-sync/previews/<Name>.tsx`, LOOK at
`ds-bundle/_screenshots/contact-sheet-*.png`, grade into
`.design-sync/.cache/review/<Name>.grade.json`, and upload with the
`DesignSync` tool. Project:
`https://claude.ai/design/p/989d5bba-1b5d-4e0d-bbee-1e0fa08d246e`.

**One-time machine setup** (`.ds-sync/` is gitignored, so a fresh clone needs
it): copy the design-sync skill's `package-build.mjs`, `package-validate.mjs`,
`package-capture.mjs`, `resync.mjs`, `lib/` and `storybook/` into `.ds-sync/`,
then `npm i esbuild ts-morph @types/react playwright` there and
`node node_modules/playwright/cli.js install chromium`. The repo's python
playwright pins a different chromium build, so this download is separate.

## Why there is a facade at all

No `package.json`, no JS build, no Storybook: the UI is Preact + htm loaded
from an importmap. The converter cannot read that, so `facade/build.mjs`
assembles a throwaway package around the same sources. Four things it does
that are not obvious, each of which failed silently first:

- **Preact renders as React through a shim, not a rewrite.** Components bind
  htm to `h` from `"preact"`; the facade resolves that specifier to
  `.design-sync/facade/preact/`, which is React wearing preact's names. No
  source file changes. The shim also translates the DOM dialect (`class` ->
  `className`, `for` -> `htmlFor`, string `style` -> object), because React
  throws on a string style and silently drops `class`.
- **The style parser must not split on a bare `;`.** Art arrives as
  `--art:url(data:image/png;base64,...)`, and a naive split shatters it. The
  tell survives every automated check: the component renders in the right
  COLOUR and the wrong SHAPE — a flat rectangle instead of a cap. Only looking
  at the sheet catches it.
- **The design system CSS is lifted out of `index.html`.** It lives in one
  `<style>` block; there is no stylesheet file to point `cssEntry` at. Losing
  that step is silent and total — every card renders on white with no tokens,
  and the render check still passes because the components themselves mount.
  `build.mjs` hard-fails if the block is missing or defines no `:root`.
- **Image assets are inlined as data URIs.** `/ui/assets/...` paths are served
  by the tracker itself and resolve to nothing inside a design. `ASSET_DIRS`
  in the registry decides what ships; a path outside it keeps its value and
  logs a warning, which shows up as a broken-image box.

## Traps to know

- **A preview cannot write a raw `/ui/assets` path.** The codemod only rewrites
  the copied component sources, not `.design-sync/previews/*.tsx`. Use the
  exported `DS_ASSET("/ui/assets/star_1.png")` helper instead.
- **`star_icons/` (1.9 MB) is deliberately not inlined.** Every component that
  draws one takes the src as a prop, so callers supply it. Adding it to
  `ASSET_DIRS` would nearly triple the bundle for no gain.
- **Overlays need a containing block.** `Modal`'s backdrop is
  `position: fixed`, so on a card it escapes its root and the capture clips to
  a strip. Its preview wraps it in a box with `transform: translateZ(0)`,
  which makes that box the containing block. The component is untouched.
- **Data shapes are not guessable.** `GroupedList` nodes are
  `{key, label, count, items, children}`, `StepPicker.required` is a `Set`,
  `Progress.prog` is `{sessions: [{points: […]}]}`. A preview that invents
  field names renders empty and trips `variants render identically` rather
  than failing outright.

## Known render warns

Both are triaged as legitimate; a re-sync showing only these is clean.

- `[TOKENS_MISSING]` — 13 custom properties (`--art`, `--mask`, `--c`,
  `--line`, `--patch-*`, `--tf-width`, …). Every one is set at runtime from JS
  inline styles, so no stylesheet defines them and none should.
- `[FONT_MISSING]` "Aptos", "Impact" — both are fallback entries inside font
  stacks, never the face a surface is designed in. SuperMario256, the real
  display face, does ship. **Not yet confirmed with Griffin** that substituting
  these two is fine.

## Re-sync risks — what can go stale

- **`StandardsPanel` can only ship its collapsed header.** It fetches its
  ladder on open, so a static card cannot honestly show more. If the Library
  needs the open ladder as a design surface, it wants a presentational split
  that takes the standards as a prop.
- **`build.mjs` patches `hat.js` by exact string match** and hard-fails if the
  anchor moves. That failure is correct and loud — re-anchor it, never drop it,
  because the silent version is 404'd cap art.
- **21 of ~45 components are published.** The rest are simply not in the
  registry. The ones deliberately left out are page-level (`Practice`, `Run`,
  `Compare`, `RankPage`) or pull the API on mount; `PbTag` is worth extracting
  out of `practice.js` first, since importing it drags the whole page in.
- **The previews carry their own dark field.** Several tiers are pale enough to
  vanish on white, so do not drop those wrappers without looking at a sheet.

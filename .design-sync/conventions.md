# SM64 Trainer — the observatory design system

A speedrun practice tool for Super Mario 64. The look is a dark "observatory":
deep navy fields, one gold accent, card-based panels, and Mario-cast art for
anything that expresses rank. Components are on `window.SM64Trainer`.

## Setup: there is no provider, but the field must be dark

No context provider, no theme wrapper, no init call — import a component and
render it. The one hard requirement is the **background**. Every component is
authored for a dark field and several rank tiers are deliberately pale
(Toadsworth, Toad); drop them on white and they read as blank boxes. Put the
page on `--bg` (or `--bg-deep` for the outermost shell) before anything else.

```jsx
<div style={{ background: "var(--bg)", color: "var(--text)", minHeight: "100vh" }}>
  <RankIcon tier="Platinum" division="II" size={28} />
</div>
```

## The styling idiom: tokens plus semantic, feature-prefixed classes

This is **not** a utility-class system. There are no `p-4`/`bg-surface-1`
helpers, and inventing them will not resolve. Two vocabularies only:

**1. Tokens** — 26 custom properties on `:root`, and the only colours you
should ever write. Use `var(--name)` verbatim:

| Group | Tokens |
|---|---|
| Fields | `--bg` `#07111e`, `--bg-deep` `#050c16` |
| Panels | `--surface` `#0c1928`, `--surface-2` `#101f31`, `--surface-3` `#14263a` |
| Lines | `--border` `#294058`, `--border-soft` `#1d3147` |
| Text | `--text` `#eef2f6`, `--muted` `#8fa0b5`, `--muted-2` `#66778d` |
| Accents | `--gold` `#f0bd32` (primary), `--gold-soft` `#ffd96a`, `--blue` `#68adf2`, `--green` `#91e5a2`, `--coral` `#ff7b73`, `--violet` `#b48bf0` |
| Meaning | `--caveat` `#e0a24a` — marks a result with a caveat, never decorative |
| Metrics | `--radius`, `--icon-size`, `--sidebar-wide`, `--sidebar-rail`, `--selector-height` |

Gold is the only accent that carries emphasis by default; the other five
distinguish sibling items that are equally important, not ranked ones.

**2. Semantic classes**, prefixed by the feature that owns them — `rank-*`,
`objective-*`, `entity-*`, `practice-*`, `segment-*`, `route-*`, `attempt-*`,
`modal-*`, `hat`. They come from the shipped stylesheet; a class you cannot
find there does not exist. For your own layout glue, write plain CSS against
the tokens rather than reaching for a class that sounds right.

## Fonts

Body copy is `Consolas, monospace` — the tool is read like an instrument
panel, not a document. `"SuperMario256"` is the display face and ships with
this bundle; use it only for numerals and short labels on rank art, never for
prose.

## Where the truth lives

`styles.css` and its `@import` closure are the whole visual system — read them
before styling anything. Each component's own contract is its `.d.ts`, and its
usage notes are its `.prompt.md`.

## Building with it

```jsx
<section style={{ background: "var(--surface)", border: "1px solid var(--border)",
                  borderRadius: "var(--radius)", padding: 16, display: "grid", gap: 12 }}>
  <header style={{ display: "flex", alignItems: "center", gap: 10 }}>
    <RankIcon tier="Diamond" division="II" size={28} />
    <span style={{ color: "var(--text)" }}>Bowser in the Dark World</span>
    <span style={{ color: "var(--muted)", marginLeft: "auto" }}>0'26"13</span>
  </header>
</section>
```

`RankIcon` is the one to reach for: it draws whichever icon style the user has
chosen. `Hat` and `Medal` are those two styles, and calling them directly pins
a choice that belongs to the user.

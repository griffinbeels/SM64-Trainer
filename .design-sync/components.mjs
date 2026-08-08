// THE registry of components published to Claude Design.
//
// One row per component, and adding a component is exactly one row. The
// facade build reads this file and generates all three things the converter
// needs from it: the bundle entry, the TypeScript contract the design agent
// codes against, and the per-component doc that becomes its `.prompt.md`.
// Nothing else in the sync pipeline names a component.
//
// `props` is the contract AND the guard: tests/test_design_sync_registry.py
// compares each row's prop names against the component's real destructured
// signature, so a prop renamed in ui/ turns the suite red instead of quietly
// leaving the design agent coding against a contract that stopped being true.
// A component that does not destructure its props declares `propsCheck` with
// the reason the guard cannot see it.

// Shared vocabulary, emitted verbatim at the top of the generated .d.ts.
export const PREAMBLE = `import type * as React from "react";

/**
 * A rank tier, hardest first. The ladder order IS this union's order and it
 * mirrors the server's own rank list. Each tier draws as a Mario-cast cap:
 * Mario, Metal, Vanish, Luigi, Wario, Waluigi, Toadsworth, Toad, Capless.
 */
export type RankTier =
  | "Mario" | "Grandmaster" | "Master" | "Diamond" | "Platinum"
  | "Gold" | "Silver" | "Bronze" | "Iron";

/** Division within a tier, bottom of the tier first. */
export type RankDivision = "V" | "IV" | "III" | "II" | "I";

/** A rank as the server hands it over. */
export interface RankBadge { rank: RankTier | null; division?: RankDivision | null; }

/**
 * Resolves one of the app's own art paths ("/ui/assets/star_1.png") to the
 * copy inlined in this bundle. Those paths are served by the tracker itself,
 * so inside a design they resolve to nothing — route any of them through
 * this and you get the real image.
 */
export declare function DS_ASSET(path: string): string;
`;

// Asset directories inlined into the bundle as data URIs. Anything the app
// serves from an absolute /ui/assets path is unreachable inside a design, so
// what is not listed here renders as a broken image. star_icons/ (1.9 MB) is
// deliberately out: every component that draws one takes the src as a PROP,
// so a caller supplies it and the weight never has to ship.
export const ASSET_DIRS = ["hat", "empty", "course_icons"];
export const ASSET_FILES = [
  "star_1.png", "star_2.png", "star_3.png",
  "star_4.png", "star_5.png", "star_6.png", "pipe_icon.png",
];

export const COMPONENTS = [
  // ---- Rank -------------------------------------------------------------
  {
    name: "RankIcon", module: "./components/rankicon.js", group: "Rank",
    also: ["ICON_STYLES", "DEFAULT_ICON_STYLE", "getRankIconStyle", "setRankIconStyle"],
    doc: "Draws a rank as an icon. THE one to reach for: it renders whichever icon style the user has chosen, so no call site learns which style is active.",
    propsCheck: { skip: "takes an opaque `props` object and forwards it whole to the active style" },
    props: [
      ["tier", "RankTier", "Which rank to draw. An unknown tier draws the neutral fallback cap."],
      ["division?", "RankDivision | null", "Division numeral, or null for a tier shown without one."],
      ["size?", "number", "Rendered height in px; width follows the cap's own aspect ratio."],
      ["title?", "string | null", "Tooltip override. Defaults to the cap's name plus its division digit."],
      ["flap?", "boolean", "Animate the cap's brim flap."],
      ["foldWings?", "number", "0..1 — folds the winged tiers' wings in."],
    ],
  },
  {
    name: "Hat", module: "./components/hat.js", group: "Rank",
    doc: "The cap icon style. Prefer RankIcon unless you must pin the style — calling this directly overrides a choice that belongs to the user.",
    props: [
      ["tier", "RankTier", "Which cap to draw."],
      ["division?", "RankDivision | null", "Division numeral shown on the cap."],
      ["size?", "number", "Rendered height in px."],
      ["title?", "string | null", "Tooltip override."],
      ["flap?", "boolean", "Animate the brim flap."],
      ["foldWings?", "number", "0..1 — folds the wings in."],
      ["growWings?", "number", "0..1 — grows the wings out, for the rank-up climb."],
      ["growProgress?", "number", "0..1 — how far through the grow the climb is."],
      ["foldProgress?", "number | null", "0..1 — how far through the fold."],
      ["flapPhase?", "number | null", "Phase of the brim flap, for hand-driven animation."],
      ["roll?", "number | null", "Degrees of roll."],
      ["squashX?", "number | null", "Horizontal squash multiplier."],
      ["squashY?", "number | null", "Vertical squash multiplier."],
      ["shake?", "number | null", "Shake amplitude."],
      ["sparkle?", "number | null", "0..1 — sparkle intensity."],
    ],
  },
  {
    name: "Medal", module: "./components/medal.js", group: "Rank",
    doc: "The medal icon style — the same ladder drawn as discs. Prefer RankIcon unless you must pin the style.",
    props: [
      ["tier", "RankTier", "Which rank the medal represents."],
      ["division?", "RankDivision | null", "Division numeral struck on the medal."],
      ["size?", "number", "Diameter in px."],
      ["title?", "string | null", "Tooltip override."],
      ["flap?", "boolean", "Accepted and unread — a medal has no brim. It exists so RankIcon can forward one prop surface to either style."],
      ["foldWings?", "number", "Accepted and unread, for the same reason: a medal has no wings."],
    ],
  },
  {
    name: "RankBanner", module: "./components/ranks.js", group: "Rank",
    also: ["RANK_MODE_OPTIONS"],
    doc: "The wide rank band a page leads with: the icon, the tier's name, and the distance to the next rung.",
    props: [
      ["label", "string", "What is being ranked, in the user's words."],
      ["banner", "RankBadge | null", "The rank to show. Null renders the unranked state."],
      ["hint?", "string | null", "One line under the band — usually what would raise the rank."],
      ["identity?", "string | null", "Stable key for the band, so a change animates instead of cutting."],
      ["atFloor?", "boolean", "True when the entity has standards but no time of its own."],
      ["lane?", "string | null", "Which animation lane the band belongs to."],
      ["order?", "number", "Position within its lane, for staggered entrances."],
      ["replayKey?", "string | number | null", "Change this to replay the entrance."],
      ["layout?", "'row' | 'stacked' | 'column'", "Which of the band's shapes to draw. The practice log leaves this alone and lets @container pick, because the choice depends on the card's own width rather than the page's."],
      ["showNext?", "boolean", "Whether the distance-to-the-next-rung line is drawn at all."],
      ["iconSize?", "number", "Pixel size the rank sprite draws itself at. A CSS variable alone only ever reserved its margin, never resized it."],
      ["nextStepMode?", "'classic' | 'hover'", "Whether the next-rung line is always visible, or revealed by hovering the progress bar."],
    ],
  },

  // ---- Selection --------------------------------------------------------
  {
    name: "PracticeCell", module: "./components/practicecell.js", group: "Selection",
    doc: "One practiced thing — a star or a segment — as a selectable cell: its icon, its name, its rank and any caveat on the result.",
    props: [
      ["active", "boolean", "Whether this cell is the current target."],
      ["iconSrc", "string", "Image for the cell. Callers supply it, so star art never has to ship in the bundle."],
      ["fallbackSlot?", "number", "Generic star to fall back to when iconSrc 404s."],
      ["rank", "RankBadge | null", "The rank to draw on the cell."],
      ["hasStandards?", "boolean", "True when a ladder exists — an unranked cell then draws the floor rather than nothing."],
      ["strat", "string | null", "Active strategy name, shown under the cell."],
      ["name", "string", "The cell's own name."],
      ["sub?", "string | null", "Secondary line — usually the time."],
      ["title?", "string | null", "Tooltip."],
      ["dimIdle?", "boolean", "Dim the cell when it is not the target."],
      ["rankBadge?", "boolean", "Draw the rank as a corner badge instead of an in-flow row."],
      ["onPick?", "() => void", "Called when the cell is chosen."],
      ["onEdit?", "() => void", "Called when the cell's edit affordance is used."],
      ["caveat?", "string | null", "Names a caveat on the result; renders the caveat mark."],
      ["subsection?", "boolean", "True when this cell is a piece of something bigger, drawn one level in from its parent."],
    ],
  },
  {
    name: "CellRow", module: "./components/cellrow.js", group: "Selection",
    also: ["SurfaceExchange"],
    doc: "A row of cells that cross-fades when its contents change identity, instead of cutting between two correct states.",
    props: [
      ["class?", "string", "Class applied to the row."],
      ["children", "React.ReactNode", "The cells."],
    ],
  },
  {
    name: "GroupedList", module: "./components/grouplist.js", group: "Selection",
    doc: "A nested list with collapsible groups. Child rows are indented one level per depth, so a parent/child relationship is visible without reading labels.",
    props: [
      ["tree", "unknown", "The `{items, children}` tree to render."],
      ["open", "Set<string>", "Paths of the expanded groups."],
      ["toggle", "(path: string) => void", "Expand or collapse one group."],
      ["renderRow", "(item: unknown) => React.ReactNode", "Renders one leaf row."],
      ["forceOpen?", "(node: unknown) => boolean", "Opens a group regardless of `open` — a search hit uses this."],
      ["depth?", "number", "Nesting depth; callers start at 0."],
    ],
  },
  {
    name: "CardSelect", module: "./components/contextselect.js", group: "Selection",
    doc: "The bare select used inside a card header, with the chevron the design system expects.",
    props: [
      ["id", "string", "Element id."],
      ["name", "string", "Form field name."],
      ["label", "string", "Accessible label."],
      ["title?", "string", "Tooltip."],
      ["options", "Array<[string, string]>", "`[value, label]` pairs."],
      ["value", "string", "Current value. Keep it listed in `options` even when a filter would drop it, or the control renders blank."],
      ["onChange", "(value: string) => void", "Called with the new value."],
    ],
  },
  {
    name: "ContextSelect", module: "./components/contextselect.js", group: "Selection",
    doc: "A labelled select with a leading icon — the course/star/strategy pickers are all this control.",
    props: [
      ["icon", "string", "Icon name shown before the label."],
      ["label", "string", "What is being chosen."],
      ["options", "Array<[string, string]>", "`[value, label]` pairs."],
      ["value", "string", "Current value; keep it in `options` unconditionally."],
      ["onChange", "(value: string) => void", "Called with the new value."],
      ["id?", "string", "Element id."],
      ["name?", "string", "Form field name."],
      ["title?", "string", "Tooltip."],
      ["empty?", "string", "What to show when there is nothing to choose."],
    ],
  },

  // ---- Standards and progress -------------------------------------------
  {
    name: "StandardsPanel", module: "./components/standards.js", group: "Standards",
    doc: "The rank ladder for one entity: every tier's time standard, with the user's own position on it. Collapsed until asked for.",
    props: [
      ["entity", "unknown", "Identity of the thing being ranked."],
      ["activeStrat", "string | null", "Strategy the standards are for."],
      ["strategies", "unknown[]", "Every strategy this entity has."],
      ["onChanged?", "() => void", "Called after the panel edits a standard."],
      ["defaultOpen?", "boolean", "Start expanded."],
      ["sectionRank?", "RankBadge | null", "Rank shown in the collapsed header."],
      ["sectionPb?", "unknown", "PB shown in the collapsed header."],
      ["family?", "unknown", "Related entities the panel offers to switch between."],
    ],
  },
  {
    name: "Progress", module: "./components/progress.js", group: "Standards",
    doc: "Every attempt in a session as a run of marks, so improvement over a sitting is visible at a glance.",
    props: [
      ["prog", "unknown", "The session's attempts."],
      ["clock", '"igt" | "rta"', "Which clock the marks are measured on."],
      ["onPick?", "(attemptId: number) => void", "Called when an attempt mark is chosen."],
    ],
  },
  {
    name: "StepChip", module: "./components/steptrack.js", group: "Standards",
    doc: "One step of a route as a chip, carrying its own done/waiting/skipped state.",
    props: [
      ["label", "string", "The step's name."],
      ["state", "string", "Visual state — done, waiting, skipped."],
      ["title?", "string", "Tooltip."],
      ["onToggle?", "() => void", "Makes the chip a button; omit for a read-only chip."],
      ["pressed?", "boolean", "Pressed state when the chip is a toggle."],
    ],
  },
  // StepTrack was published here until 2026-08-06. The app deleted it (the
  // practice card no longer draws a run's step cursor), so the row goes with
  // it -- a published component with no implementation is a contract the
  // design agent can code against and nobody can render.
  {
    name: "StepPicker", module: "./components/steptrack.js", group: "Standards",
    doc: "Choose which of a route's steps are required.",
    props: [
      ["steps", "Array<{ node: string; label: string; sentence: string }>", "Every place the journal says you walked through. Empty renders nothing."],
      ["required", "Set<string>", "Node keys this movement requires. Empty is legal — it means any route counts."],
      ["onToggle", "(node: string) => void", "Flips one step between required and merely passed through."],
    ],
  },

  // ---- Shell and states -------------------------------------------------
  {
    name: "Modal", module: "./components/modal.js", group: "Shell",
    doc: "The dialog shell. Everything inside is the caller's; the shell owns the title row, the close affordance and focus.",
    props: [
      ["title", "string", "Dialog title."],
      ["description?", "string", "One line under the title."],
      ["icon?", "string", "Icon name for the title row."],
      ['size?', '"small" | "medium" | "large"', "Panel width."],
      ["onClose", "() => void", "Called on close. A dialog that cannot be abandoned without side effects is a bug."],
      ["footer?", "React.ReactNode", "Actions row."],
      ["children", "React.ReactNode", "Dialog body."],
    ],
  },
  {
    name: "CollapseToggle", module: "./components/collapsible.js", group: "Shell",
    doc: "The disclosure control on a card header, with the label baked into its tooltip.",
    props: [
      ["collapsed", "boolean", "Current state."],
      ["toggle", "() => void", "Flips it."],
      ["label", "string", "What is being collapsed, for the tooltip."],
    ],
  },
  {
    name: "EmptyState", module: "./components/emptystate.js", group: "Shell",
    doc: "What a panel shows when it has nothing yet: a member of the SM64 cast, what is missing, and the one action that fills it.",
    props: [
      ["headline", "string", "What is missing, in the panel's own terms."],
      ["hint?", "string", "The one action that fills it."],
    ],
  },
  {
    name: "PageState", module: "./components/states.js", group: "Shell",
    doc: "A whole card standing in for content that is loading or unreachable.",
    props: [
      ['kind?', '"loading" | "offline"', "Which state to show."],
      ["title?", "string", "Headline."],
      ["message?", "string", "One explanatory line."],
    ],
  },
  {
    name: "InlineState", module: "./components/states.js", group: "Shell",
    doc: "The same idea at one line, for a state that does not deserve a whole card.",
    props: [
      ['kind?', '"loading" | "error"', "Which state to show."],
      ["children", "React.ReactNode", "The message."],
    ],
  },
  {
    name: "Icon", module: "./components/icons.js", group: "Shell",
    doc: "The icon set. Stroked, currentColor, sized in px — never a raster.",
    props: [
      ["name", "string", "Which glyph."],
      ["size?", "number", "Square size in px."],
      ["className?", "string", "Extra classes."],
    ],
  },

  // ---- Input ------------------------------------------------------------
  {
    name: "TimeFields", module: "./components/timefields.js", group: "Input",
    doc: "Entering a time as minutes, seconds and centiseconds rather than one string, so a mistyped digit is local.",
    props: [
      ["seconds", "number", "Current value in seconds."],
      ["onCommit", "(seconds: number) => void", "Called once the three boxes settle."],
      ["compact?", "boolean", "Tighter layout for a table row."],
      ["label?", "string", "Accessible label."],
    ],
  },
];

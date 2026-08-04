// src/sm64_events/ui/logtuning.js — THE registry of every number and shape
// choice that decides how a practice-log `LogCard` LOOKS.
//
// Same contract as climbtuning.js/marelotuning.js, and for the same reason
// (user, 2026-07-27: "like how I would work with an Inspector in Godot"). It
// exists because the shipped log read as "disorganized / sloppy" and every
// complaint behind that word was a LAYOUT judgement, not a logic bug:
//
//   1. collapsed cards don't line up (icon/name sit at a different x per card)
//   2. a closed card is vertically massive at a narrow window
//   3. a closed card at a WIDE window should look the same, just bigger
//   4. an open card's attempt rows don't read as belonging to it
//
// One structural difference from the climb registries: nothing here is a
// DURATION consumed by a JS animation loop. Every row here is consumed by CSS,
// through ONE custom property per numeric tunable (`--log-<kebab-key>`) and
// ONE modifier class per choice (`<row.cssPrefix>-<value>`, e.g.
// `log-rankstyle-chips`) — both generated here, never hand-named at a call
// site, so a second file cannot invent a var name that drifts from this one.
// `logTuningVars`/`logTuningClasses` are the two pure functions that do it;
// `ui/components/practicelog.js` calls them ONCE, at the wiring layer (the
// page-level `PracticeLog`), and every `.log-card` underneath inherits the
// custom properties for free (they cascade) and matches the class-scoped CSS
// rules by being a descendant — `LogCard` itself never imports this file.
//
// Import-free, like its neighbours, so node can execute it directly
// (tests/test_ui_logtuning.py).
//
// ---- A row -----------------------------------------------------------------
//
//   group   which section of the inspector it appears under
//   label   what the control is called
//   value   THE SHIPPED DEFAULT — my best reading of the four complaints
//           above, not a copy of what shipped before this registry existed
//           (unlike climbtuning.js's rows, which WERE exactly the old
//           constants: there the goal was a byte-identical extraction, here
//           the goal was fixing four reported layout bugs, and the tuning
//           rig is how the human pushes those defaults past good to find out
//           where good actually was).
//   min/max/step  the control's range, deliberately wider than anything
//           sensible.
//   unit    "px" | "x" | "" — display only. "x" is a 0..1 fraction/multiplier.
//   why     one line, the control's tooltip.

export const TUNABLES = {
  // ---- Identity: the course icon + name column ---------------------------
  iconSize: {
    group: "Identity", label: "Course icon size", value: 40,
    min: 20, max: 96, step: 1, unit: "px",
    why: "The course/star icon in the card head. Bigger is the point at a wide window, once the ranks column stops needing a whole banner's worth of room.",
  },
  nameSize: {
    group: "Identity", label: "Name size", value: 13,
    min: 10, max: 32, step: 1, unit: "px",
    why: "Font size of the entity's own name — the biggest text in the head row.",
  },
  courseSize: {
    group: "Identity", label: "Course label size", value: 8,
    min: 8, max: 20, step: 1, unit: "px",
    why: "The small course/context line sitting above the name.",
  },
  identityGap: {
    group: "Identity", label: "Icon-to-name gap", value: 10,
    min: 0, max: 40, step: 1, unit: "px",
    why: "Space between the icon and the name column.",
  },
  identityWidth: {
    group: "Identity", label: "Identity column width", value: 245,
    min: 140, max: 520, step: 5, unit: "px",
    why: "A FIXED track, not a fraction of the card. This is the whole fix for item 1 — every card's icon lands on the same x because every card reserves the same width for it, regardless of what else is on that card's own row. Too narrow and a long star name ellipsises (tests/test_log_card_name_fits.py's own corpus tops out at 30 characters).",
  },

  // ---- Ranks: the strategy + star/segment rank display --------------------
  rankWidth: {
    group: "Ranks", label: "Rank column width", value: 420,
    min: 80, max: 420, step: 5, unit: "px",
    why: "How wide each rank banner may grow. Ships tight because Caps only (the default rank display) needs almost none of it; widen this first if you dial Rank display up to Chips or Banners, which need real room for text.",
  },
  rankBarHeight: {
    group: "Ranks", label: "Progress bar height", value: 5,
    min: 2, max: 20, step: 1, unit: "px",
    why: "Height of the within-division progress bar. Only visible in Banners mode — Chips and Caps-only hide it entirely.",
  },
  rankGap: {
    group: "Ranks", label: "Gap between the two ranks", value: 0,
    min: 0, max: 40, step: 1, unit: "px",
    why: "Space between the strategy rank and the star/segment rank, whichever direction they're laid out (side by side in One line/Two line, stacked in Stacked).",
  },

  // ---- Result: the PB tag ---------------------------------------------------
  pbWidth: {
    group: "Result", label: "PB column min-width", value: 92,
    min: 40, max: 240, step: 4, unit: "px",
    why: "Minimum width reserved for the PB tag, so the chevron next to it doesn't jump left and right as the saved time gets longer or shorter.",
  },
  pbSize: {
    group: "Result", label: "PB text size", value: 13,
    min: 9, max: 28, step: 1, unit: "px",
    why: "Font size of the PB tag in the card head.",
  },

  // ---- Open state: the attempt table once a card is expanded --------------
  bodyIndent: {
    group: "Open state", label: "Body indent", value: 50,
    min: 0, max: 80, step: 2, unit: "px",
    why: "Extra left padding on the attempt table once a card is open — item 4's whole fix: the rows read as belonging to the head above them, not floating beside it.",
  },
  bodyTint: {
    group: "Open state", label: "Body darkness", value: 0.16,
    min: 0, max: 1, step: 0.02, unit: "x",
    why: "How much darker the open body is than the card around it — a black wash over the SAME background colour, so the rows read as sitting UNDER the card rather than as a different surface. 0 removes the wash entirely.",
  },
  bodyGap: {
    group: "Open state", label: "Head-to-body gap", value: 4,
    min: 0, max: 60, step: 2, unit: "px",
    why: "Breathing room between the divider under the head and the first attempt row.",
  },
  rowHeight: {
    group: "Open state", label: "Attempt row height", value: 40,
    min: 24, max: 96, step: 2, unit: "px",
    why: "Minimum height of one attempt row. A row's own content can still grow past this; it never shrinks below it.",
  },

  // ---- Density: the log list itself ----------------------------------------
  cardGap: {
    group: "Density", label: "Gap between cards", value: 2,
    min: 0, max: 40, step: 1, unit: "px",
    why: "Vertical space between one entity's card and the next in the log.",
  },
  headPadX: {
    group: "Density", label: "Head padding (sides)", value: 14,
    min: 4, max: 40, step: 1, unit: "px",
    why: "Left/right padding of the card's head row.",
  },
  headPadY: {
    group: "Density", label: "Head padding (top/bottom)", value: 10,
    min: 4, max: 40, step: 1, unit: "px",
    why: "Top/bottom padding of the card's head row — most of what makes the row feel cramped or roomy.",
  },
};

/** Non-numeric knobs: shape and style choices, each backed by a modifier
 *  class (`cssPrefix-value`) on the log list's own wrapper rather than a
 *  custom property — index.html's rules select on the class, never on a
 *  var(), so a choice can restructure the grid instead of only resizing it. */
export const CHOICES = {
  collapsedLayout: {
    group: "Shape", label: "Collapsed layout", value: "oneLine",
    options: {
      oneLine: "One line (icon, name, ranks, PB, chevron)",
      twoLine: "Two lines (identity above, ranks + PB below)",
      stacked: "Stacked (today's narrow layout, at any width)",
    },
    cssPrefix: "log-layout",
    why: "How a card's head arranges itself. Open vs. closed makes no difference — the head is always visible; this is items 2 and 3's fix, a shape that reads the same condensed way at every width.",
  },
  identityAlign: {
    group: "Shape", label: "Identity alignment", value: "left",
    options: { left: "Left-aligned", centred: "Centred" },
    cssPrefix: "log-align",
    why: "Where the icon and name sit inside their own fixed-width column.",
  },
  widthMode: {
    group: "Shape", label: "Width behaviour", value: "same",
    options: {
      same: "Same layout at every width",
      stackNarrow: "Switch to Stacked below 900px",
    },
    cssPrefix: "log-width",
    why: "\"Maybe this can just be the same layout in both screen sizes\" (user) — Same holds whatever Collapsed layout is picked above at every supported width; Stack narrow reintroduces a width-triggered switch to Stacked, for comparison.",
  },
  rankStyle: {
    group: "Ranks", label: "Rank display", value: "banners",
    options: {
      banners: "Full banners (bar + next-step line)",
      chips: "Chips (icon + rank name only)",
      capsOnly: "Caps only (icon, no text)",
    },
    cssPrefix: "log-rankstyle",
    why: "How much of each rank banner shows in the card head. Ships at Caps only: two full rank NAMES (up to \"Grandmaster 1\", 13 characters, each) cannot both fit beside an icon, a long star name, a PB tag and a chevron on one line at the app's 850px supported floor without either truncating one of them or growing the card past what the floor allows — measured against the real rank corpus, not guessed. Caps only is the one setting that has NO text to truncate at any width; Chips is fully real and worth dialing up once Rank column width is opened wider than the shipped default, on a window wide enough to afford it.",
  },
};

export const DEFAULTS = Object.freeze(Object.fromEntries([
  ...Object.entries(TUNABLES).map(([key, row]) => [key, row.value]),
  ...Object.entries(CHOICES).map(([key, row]) => [key, row.value]),
]));

/** Every group, in declaration order — the inspector's section order. */
export const GROUPS = [...new Set([
  ...Object.values(CHOICES).map((row) => row.group),
  ...Object.values(TUNABLES).map((row) => row.group),
])];

// A tunable a caller did not name falls back to the shipped default, and an
// UNKNOWN key is dropped rather than carried through — the same two rules
// marelotuning.js's own withMareloDefaults states (climbtuning.js's original
// withDefaults lacked the second half and shipped a real bug the day a row
// was deleted: a stale localStorage draft kept resurrecting it).
export const withDefaults = (values) => ({
  ...DEFAULTS,
  ...Object.fromEntries(Object.entries(values || {})
    .filter(([key]) => key in DEFAULTS)),
});

// ---- The ACTIVE tuning -----------------------------------------------------
//
// One module-level slot, read ONCE by the wiring layer (practicelog.js's
// `PracticeLog`) — never inside `LogCard` itself, and never per attempt-row
// render. Nothing else reads it.
let active = withDefaults(null);
export const logTuning = () => active;
export function setLogTuning(values) {
  active = withDefaults(values);
  return active;
}

// camelCase registry key -> the CSS custom property name it drives.
// iconSize -> --log-icon-size. THE one place this mapping is spelled out —
// both logTuningVars (below) and tests/test_ui_logtuning.py's reader-scan
// derive it from here rather than restating it, so a rename can't drift the
// two apart.
export function cssVarName(key) {
  return `--log-${key.replace(/([A-Z])/g, "-$1").toLowerCase()}`;
}

/** Every TUNABLE as a `{ "--log-icon-size": "40px", ... }` style object,
 *  ready to spread onto an element's `style`. "x"-unit rows (fractions/
 *  multipliers) are written bare; everything else gets a "px" suffix — there
 *  is no third unit among today's rows, and a new one should extend this
 *  rather than grow a per-row unit table CSS would also have to know about. */
export function logTuningVars(values = DEFAULTS) {
  const merged = withDefaults(values);
  return Object.fromEntries(Object.entries(TUNABLES).map(([key, row]) =>
    [cssVarName(key), row.unit === "x" ? String(merged[key]) : `${merged[key]}px`]));
}

/** Every CHOICE as a space-joined modifier-class string, e.g.
 *  "log-layout-oneline log-align-left log-width-same log-rankstyle-chips".
 *  The class suffix is the option KEY lower-cased ("oneLine" -> "oneline") —
 *  CSS classes read as one lowercase word here, matching every other
 *  modifier class in index.html; the registry's own option keys stay
 *  camelCase because that is this file's convention everywhere else. */
export function logTuningClasses(values = DEFAULTS) {
  const merged = withDefaults(values);
  return Object.entries(CHOICES)
    .map(([key, row]) => `${row.cssPrefix}-${String(merged[key]).toLowerCase()}`)
    .join(" ");
}

// ---- The settings string ----------------------------------------------------
//
// EVERY value, not just the ones that differ from the shipped defaults — same
// reasoning as climbtuning.js's own encodeTuning: encoding only the
// differences would silently re-point an old string at new defaults the day
// one of them is codified.
export const encodeTuning = (values) => JSON.stringify(
  Object.fromEntries(Object.keys(DEFAULTS).sort()
    .map((key) => [key, withDefaults(values)[key]])));

/** Parse a settings string. Unknown keys are DROPPED, not carried. */
export function decodeTuning(text) {
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== "object") throw new Error("not a settings object");
  return withDefaults(Object.fromEntries(
    Object.entries(parsed).filter(([key]) => key in DEFAULTS)));
}

/** Only what differs from the shipped defaults — the thing worth codifying. */
export const changedFromDefault = (values) =>
  Object.fromEntries(Object.entries(withDefaults(values))
    .filter(([key, value]) => value !== DEFAULTS[key]));

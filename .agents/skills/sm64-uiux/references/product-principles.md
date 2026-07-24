# SM64 Trainer product and design principles

## Product promise

SM64 Trainer is a companion for the player's improvement journey, not an admin
dashboard. It should make practice feel inviting, legible, energetic, and rewarding
without interrupting play.

The first-view questions are:

1. What am I practicing now?
2. Is the trainer recording it?
3. How did the last attempt go?
4. What is the next useful goal?

Everything else can remain close at hand without competing with those answers.

## Information hierarchy

1. **Practice now:** course/star or segment, strategy, live/armed state, clock.
2. **Feedback:** latest result, PB, rank, next rank, meaningful success.
3. **Analysis:** trend, timeline, attempts, stats, filters, replay actions.
4. **Workshop:** route and segment authoring, rank standards, detailed settings.
5. **System:** sessions, updates, restart, recording capacity, diagnostics, wiping.

System status may stay visible when important, but system operations must not dominate
the practice surface. Destructive actions belong behind explicit labels and
confirmation.

## Personality

- Extend the approved star selector's warmth, tactility, and sense of reward.
- Choose one coherent metaphor per direction: observatory, castle journal, toybox,
  or another user-approved idea. Do not mix every Mario reference together.
- Evoke an N64-era adventure through shape, color, rhythm, and interaction rather
  than copying an original game menu wholesale.
- Use Mario primary colors as accents with strong semantic roles, not as equal-area
  decoration.
- Prefer a friendly display face for identity, a highly readable UI face for
  controls, and monospace/tabular numerals for times. Do not use monospace for the
  entire product.
- Let precise spacing, alignment, and hierarchy carry more craft than surface
  decoration.

## Delight and motion

- Celebrate meaningful events: a new PB, new rank, route completion, or successful
  attempt.
- Keep feedback brief, interruptible, and close to the object it describes.
- Preserve the existing star-selector behavior during the initial shell overhaul
  unless the user explicitly changes it.
- Respect `prefers-reduced-motion`. Never make motion the sole status indicator.
- Avoid ambient pulsing and perpetual animation outside genuinely live states.

## Layout stability and capture safety

The practice surface is often cropped into OBS and watched peripherally while the
player controls the game. Once the user sizes the window, normal live-state changes
must not reorganize the page.

- Keep permanent layout slots for stage/target selection, the active objective,
  analysis, and recent attempts. Swap content inside those slots instead of
  mounting banners or moving whole sections.
- Star-to-star, star-to-segment, armed-to-disarmed, and target-to-no-target changes
  must preserve the outer position and practical height of those slots.
- Put running/armed feedback in a reserved cell inside the active-objective slot;
  never insert a new banner above the practice content.
- Give charts stable plotting dimensions and attempt rows predictable heights.
  Use internal scrolling or explicit expansion for variable-length history.
- Constrain long names and optional metadata inside their allocated regions through
  wrapping, truncation, or progressive disclosure; they must not push unrelated
  cards.
- Use tabular numerals and stable metric columns so changing times do not nudge
  adjacent information.
- Responsive breakpoint changes may reflow the grid after an actual window resize.
  Live game and target state changes at a fixed viewport may not.
- Preserve useful crop regions for the active objective, analysis, and attempts.
  Empty states retain the same slot geometry rather than collapsing it.

## Novice and expert use

- Put the main practice action in the first viewport without setup menus.
- Use the player's vocabulary. Explain "IGT", "segment", and unfamiliar modes
  in context rather than assuming prior knowledge.
- Favor recognition: show the active objective and available next actions where they
  are used.
- Keep expert controls reachable through clearly labeled secondary toolbars, menus,
  or expandable analysis—never mystery glyphs.
- Remember user choices and do not force repeated configuration.

## Anti-slop review

Reject a concept or implementation that:

- looks like a generic SaaS dashboard or marketing page;
- turns every element into a bordered rounded card;
- uses pills, badges, tiny uppercase labels, gradients, or glows as filler;
- invents a mascot, fake coaching copy, fake metrics, or fake product features;
- changes only the palette while retaining the same hierarchy;
- hides core functionality for cosmetic minimalism;
- gives primary, secondary, destructive, and system actions equal visual weight;
- uses decorative game art that reduces text clarity or control affordance;
- ships inconsistent spacing, icon weight, typography, or interaction states.

## Accessibility floor

- Provide keyboard access and a visible focus state for every control.
- Use clear interactive signifiers and sufficiently large click targets.
- Maintain readable contrast and default text sizes.
- Pair semantic color with text, icon, shape, or position.
- Verify narrow layouts without clipped controls or horizontal page scrolling.
- Keep animation optional and avoid flicker.

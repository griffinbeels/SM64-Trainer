// src/sm64_events/ui/shrinkfit.js
//
// The pure half of the `shrinkToFit` value of logtuning.js's `nameOverflow`
// choice (Option 1, spec 2026-08-04-rank-variants): given what a name
// actually measures, how big should its font render? Import-free, like
// climbcurve.js/climbplan.js/caps.js -- so tests/test_ui_shrinkfit.py can
// drive it directly under node, the same reason those modules stay
// import-free (the DOM-measuring half, ui/components/shrinkname.js, pulls
// in Preact and is verified by rendering instead).

// Below this, the text stops being worth reading -- the floor exists only to
// stop a name far longer than anything the real corpus carries from shrinking
// toward 1px; ordinary corpus names settle well above it.
export const MIN_FIT_PX = 8;

/** What font size makes `naturalWidth` (the text's own rendered width at
 *  `baseSizePx`) fit inside `availableWidth`? Text width scales roughly
 *  linearly with font size, so one ratio gets most of the way there --
 *  `ShrinkToFitName` applies this TWICE (the second pass corrects for the
 *  small non-linearity a real font's kerning/hinting introduces), never in
 *  an open-ended loop -- so this stays a single, settled calculation rather
 *  than a step-by-step search. Never returns above `baseSizePx`: a name that
 *  already fits keeps its exact tuned size, byte-identical to `ellipsis`'s
 *  own unshrunk text. */
export function fittedFontSize(baseSizePx, availableWidth, naturalWidth, minSizePx = MIN_FIT_PX) {
  if (!(naturalWidth > 0) || !(availableWidth > 0) || naturalWidth <= availableWidth) {
    return baseSizePx;
  }
  const scaled = baseSizePx * (availableWidth / naturalWidth);
  return Math.max(minSizePx, Math.min(baseSizePx, scaled));
}

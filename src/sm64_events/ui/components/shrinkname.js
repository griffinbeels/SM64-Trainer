// src/sm64_events/ui/components/shrinkname.js
//
// The `shrinkToFit` value of logtuning.js's `nameOverflow` choice (Option 1,
// spec 2026-08-04-rank-variants): step `.log-card-name b`'s font size down
// until the name fits its column, instead of ellipsizing or wrapping.
//
// CSS has nothing that sizes text by its own rendered length, so this is a
// measure-then-write component -- one of the deliberate cracks in
// logtuning.js's "everything here is consumed by CSS" contract, alongside
// `rankIconSize` (a JS number the Hat/Medal sprite draws itself at). The
// actual sizing MATH is import-free (`ui/shrinkfit.js::fittedFontSize`), the
// same split climbcurve.js/rankclimb.js already draw between "how fast" and
// "the rAF loop that applies it" -- this file is only the DOM-measuring half.
//
// Off the render path on purpose, per CLAUDE.md's brief for this feature:
//   * it must not run on every render -- a ResizeObserver fires only when the
//     name's own box actually changes width, never on an unrelated re-render
//     of LogCard (attempts changing, the card opening/closing, a sibling
//     card's rank climbing);
//   * it must not fight the climb animation -- it never touches anything
//     `useRankClimb` owns; the name it measures is plain text, not a rank;
//   * it must settle -- `useLayoutEffect` runs synchronously before paint, so
//     there is no visible flash of untruncated-then-shrunk text, and the
//     measurement itself is bounded at exactly two passes (see
//     `fittedFontSize`'s own docstring), never a loop that could fail to
//     converge.
import { h } from "preact";
import { useLayoutEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { MIN_FIT_PX, fittedFontSize } from "../shrinkfit.js";

const html = htm.bind(h);

export { MIN_FIT_PX, fittedFontSize };

/** `enabled=false` renders exactly the plain, unshrunk shape -- no inline
 *  style at all (Preact omits a `null` style attribute entirely), so the
 *  `ellipsis`/`wrap` options stay byte-identical to a plain `<b>${text}</b>`.
 *  The base (CSS-tuned) size is read via `getComputedStyle` rather than
 *  threaded in as a prop: `--log-name-size` already reaches this element
 *  through the cascade, so re-deriving the pixel number here would be a
 *  second place that has to agree with the registry's own tunable. */
export function ShrinkToFitName({ text, enabled, minSizePx = MIN_FIT_PX }) {
  const ref = useRef(null);
  const [sizePx, setSizePx] = useState(null);

  useLayoutEffect(() => {
    if (!enabled || !ref.current) {
      setSizePx(null);
      return undefined;
    }
    const el = ref.current;
    const measure = () => {
      // Clear any prior shrink before measuring "natural" -- otherwise a
      // later pass would measure an ALREADY-shrunk box and compound toward
      // zero instead of settling.
      el.style.fontSize = "";
      const basePx = parseFloat(getComputedStyle(el).fontSize) || 0;
      const available = el.clientWidth;
      const natural = el.scrollWidth;
      const first = fittedFontSize(basePx, available, natural, minSizePx);
      el.style.fontSize = `${first}px`;
      // One correction pass against the now-shrunk box: a font-size change
      // does not scale scrollWidth PERFECTLY linearly (kerning/hinting), so
      // the first guess can still be a few px off. A second pass against
      // the corrected size is what actually settles it -- never a third,
      // which is how this would stop being guaranteed to end.
      const secondAvailable = el.clientWidth;
      const secondNatural = el.scrollWidth;
      const settled = secondNatural > secondAvailable
        ? fittedFontSize(first, secondAvailable, secondNatural, minSizePx)
        : first;
      el.style.fontSize = `${settled}px`;
      setSizePx(settled);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(el.parentElement || el);
    return () => observer.disconnect();
  }, [enabled, text, minSizePx]);

  return html`<b ref=${ref} style=${sizePx != null ? `font-size:${sizePx}px` : null}>${text}</b>`;
}

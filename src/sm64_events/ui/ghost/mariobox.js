// src/sm64_events/ui/ghost/mariobox.js — WHERE MARIO'S RECTANGLE LANDS.
//
// His words, 2026-09-20: *"let's do tight rectangles on our existing ghosts,
// let's try it."* The overlay can draw only the part of the footage Mario is
// in; this module is the arithmetic that puts that part exactly where the
// whole picture would have put it, and nothing else.
//
// PURE and import-free on purpose. Every number below is a claim about where
// a pixel goes, and the only honest way to check one is to compute it
// somewhere a test can read it. The page does the drawing; this decides what
// to draw and where.
//
// THREE PIXEL SPACES, and mixing them is the whole risk:
//
//   the LIST's space   the picture the boxes were measured in (its own
//                      `width`/`height`). SAM 2.1 ran on a decode of the clip
//                      and there is no rule that it matched the browser's.
//   the VIDEO's space  `videoWidth`/`videoHeight`, what `drawImage` cuts from.
//   the CANVAS's space CSS pixels of the overlay window, where it lands.
//
// The list's space is converted to the video's by ratio, and the video's to
// the canvas's by the SAME transform `object-fit: contain` + `object-position:
// center` applies to the `<video>` beside it. That is what makes the cut-out
// land exactly where whole-picture footage would: both views are the same
// mapping, and one of them just stops drawing the scenery.

// How much of the footage around Mario comes with him. A rectangle from a
// detector is TIGHT -- it ends at his hat and his shoes -- and a cut-out with
// nothing around it reads as a sticker rather than as a player. His own words
// for the v0 were "a super tight rectangle", so this is deliberately small: a
// fraction of the box's LARGER side, so a tall thin Mario and a wide diving
// Mario get a margin in proportion to him rather than to the picture.
//
// A number he will want to move once he has seen it. It is one constant in one
// place, reported in `ghostStatus()` so a live look says what it was.
export const MARGIN_FRACTION = 0.08;

/**
 * Where the clip's picture lands inside a box, the way the browser puts it.
 *
 * This is `object-fit: contain` with `object-position: center` written out:
 * scale to the largest size that fits, centre the remainder. The `<video>` and
 * the canvas are the same box (`inset: 0`), so reproducing the browser's own
 * rule here is what keeps the two views registered on each other -- and it is
 * measurable, which `object-fit` alone is not.
 */
export function containRect(boxWidth, boxHeight, pictureWidth, pictureHeight) {
  if (!(boxWidth > 0 && boxHeight > 0
        && pictureWidth > 0 && pictureHeight > 0)) return null;
  const scale = Math.min(boxWidth / pictureWidth, boxHeight / pictureHeight);
  const width = pictureWidth * scale;
  const height = pictureHeight * scale;
  return { x: (boxWidth - width) / 2, y: (boxHeight - height) / 2,
           width, height, scale };
}

/**
 * Mario's rectangle with its margin, clamped to the picture it came from.
 *
 * In the LIST's own space, so it is comparable with the list's `width` and
 * `height` and with nothing else. Clamped rather than centred-and-clipped: a
 * Mario at the edge of the screen keeps the margin he can have and loses the
 * margin that would be off the picture, which is what the footage itself does.
 */
export function grownBox(box, list, marginFraction = MARGIN_FRACTION) {
  if (!box || !list || !(list.width > 0 && list.height > 0)) return null;
  const [x, y, width, height] = box;
  if (!(width > 0 && height > 0)) return null;
  const margin = Math.max(0, Math.round(marginFraction
                                        * Math.max(width, height)));
  const left = Math.max(0, x - margin);
  const top = Math.max(0, y - margin);
  const right = Math.min(list.width, x + width + margin);
  const bottom = Math.min(list.height, y + height + margin);
  if (!(right > left && bottom > top)) return null;
  return { x: left, y: top, width: right - left, height: bottom - top };
}

/**
 * The one `drawImage` call for this picture: what to cut out of the video, and
 * where to put it on the canvas. Null when there is nothing to draw -- Mario
 * is not in this picture, the box is unusable, or the element has no picture
 * size yet.
 *
 * `picture` is the VIDEO's own size (`videoWidth`/`videoHeight`) and `rect` is
 * `containRect` for the canvas. The list's coordinates are converted into the
 * video's by ratio, which is a no-op whenever the maker measured the same
 * decode -- and is the difference between a cut-out on Mario and one beside
 * him when he did not.
 */
export function marioDraw(box, list, picture, rect,
                          marginFraction = MARGIN_FRACTION) {
  const grown = grownBox(box, list, marginFraction);
  if (!grown || !rect || !picture
      || !(picture.width > 0 && picture.height > 0)) return null;
  const kx = picture.width / list.width;
  const ky = picture.height / list.height;
  const sx = grown.x * kx;
  const sy = grown.y * ky;
  const sw = grown.width * kx;
  const sh = grown.height * ky;
  // `rect.scale` maps the video's pixels onto the canvas; using it rather than
  // a second division is what guarantees the cut-out and the whole picture
  // agree to the last pixel.
  return { sx, sy, sw, sh,
           dx: rect.x + sx * rect.scale,
           dy: rect.y + sy * rect.scale,
           dw: sw * rect.scale,
           dh: sh * rect.scale };
}

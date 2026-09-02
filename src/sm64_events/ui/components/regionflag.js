// src/sm64_events/ui/components/regionflag.js — the ONE place a game region
// (JP or US) turns into something on screen (round 24 item 3, his words:
// "everywhere we say 'JP' or 'US', just use the flag for JP or US instead.
// that's kinda more swag").
//
// Two facts this file owns, and `tests/test_single_source.py`'s "a game
// region drawn as a flag" row keeps any other file from naming either:
//
//  1. The ASSET PATHS. A flag has an authoritative form, so it is fetched,
//     never drawn (2026-07-28's brand-mark ruling, after three of five
//     hand-authored logos came back wrong). `ui/assets/flag_us.svg` and
//     `flag_jp.svg` are held VERBATIM as fetched from flag-icons
//     (github.com/lipis/flag-icons, MIT) — the 4x3 set, which is what
//     `FLAG_RATIO` below encodes. TRUE RECTANGLES, and that is the whole
//     reason this is not the Twemoji set it shipped with for an hour: those
//     draw each flag as a rounded rect inside a SQUARE box, so a square
//     <img> left transparent bands above and below and the corners were
//     visibly clipped — "Both flags should be rectangular, since that's
//     what flag shaped means. No white borders like that" (2026-09-02).
//     And NOT the emoji either: Windows' Segoe UI Emoji has no flag glyphs
//     at all and renders 🇺🇸 as the two letters "US", so on his machine the
//     emoji would have shipped the exact text this round is replacing.
//  2. The WORD. `regionLabel` is the fallback every flag carries in its
//     `alt` and `title`, so the picture is never the only carrier of which
//     region a thing belongs to — a screen reader, a failed asset load and
//     a hover all still say "JP" or "US".
//
// Import-free of the store and of api.js, the same discipline
// versionswitch.js and librarymodel.js hold to, so node can drive it.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

// Every region this project knows, in the order a control lists them --
// JP left, US right, the layout his 2026-08-15 ruling put the switch in and
// the one `tests/test_ui_version_switch.py` pins.
export const REGIONS = ["jp", "us"];

const FLAG_SRC = { us: "/ui/assets/flag_us.svg", jp: "/ui/assets/flag_jp.svg" };
// Both assets are the 4x3 set, so one number sizes a flag and the other
// follows. Callers pass the WIDTH.
const FLAG_RATIO = 3 / 4;

/** "US" | "JP" — the word behind the picture. Anything unrecognised falls
 * back to US, the same default `library.js` and `ratings.py` already use. */
export function regionLabel(version) {
  return String(version).toLowerCase() === "jp" ? "JP" : "US";
}

/**
 * version  "jp" | "us".
 * size     the flag's WIDTH in px; the height follows at 4:3.
 * title    hover text; defaults to the region's own word, which is what
 *          makes a bare flag self-explaining on every surface.
 */
export function RegionFlag({ version, size = 20, title = null, className = "" }) {
  const key = String(version).toLowerCase() === "jp" ? "jp" : "us";
  const word = regionLabel(key);
  return html`<img class=${`region-flag ${className}`.trim()} src=${FLAG_SRC[key]}
      width=${size} height=${Math.round(size * FLAG_RATIO)}
      alt=${word} title=${title || word} draggable="false" />`;
}

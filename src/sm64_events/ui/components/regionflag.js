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
//     `flag_jp.svg` are held VERBATIM as fetched from the Twemoji set
//     (github.com/jdecked/twemoji, graphics CC-BY 4.0) — the same set the
//     Unicode flag emoji themselves are drawn from on most platforms.
//     And NOT the emoji: Windows' Segoe UI Emoji has no flag glyphs at all
//     and renders 🇺🇸 as the two letters "US", so on his machine the emoji
//     would have shipped the exact text this round is replacing.
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

/** "US" | "JP" — the word behind the picture. Anything unrecognised falls
 * back to US, the same default `library.js` and `ratings.py` already use. */
export function regionLabel(version) {
  return String(version).toLowerCase() === "jp" ? "JP" : "US";
}

/**
 * version  "jp" | "us".
 * size     px, both dimensions (the source viewBox is square-ish 36x36 and
 *          both flags are drawn to the same frame, so one number is enough).
 * title    hover text; defaults to the region's own word, which is what
 *          makes a bare flag self-explaining on every surface.
 */
export function RegionFlag({ version, size = 16, title = null, className = "" }) {
  const key = String(version).toLowerCase() === "jp" ? "jp" : "us";
  const word = regionLabel(key);
  return html`<img class=${`region-flag ${className}`.trim()} src=${FLAG_SRC[key]}
      width=${size} height=${size} alt=${word} title=${title || word}
      draggable="false" />`;
}

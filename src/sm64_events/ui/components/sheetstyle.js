// src/sm64_events/ui/components/sheetstyle.js -- how your pasted sheet column
// is coloured.
//
// ROUND 29 item 2, his words: "people like to color the cells either an EMU
// color or an N64 color... The user should be able to select a color for N64
// times, and select a color for EMU times... I guess you should also be able to
// choose the font color... it would be nice to support the default set of
// Google Sheets fonts." Three colours and a font, ONE stored preference
// (`GET`/`PUT /api/scorecard/sheet_style`), drawn here as an inspector: a
// control per value and a live preview of the two cells it produces, so the
// look is tuned by eye rather than argued about -- the defaults are only the
// starting point ("a nice blue / orange combination, probably darker than
// Raisn's setup"). The Copy sheet column button reads the stored style at
// copy time, so a change here paints the very next paste.
//
// ROUND 30 rebuilt the CONTROLS after he opened it, and MOVED them: they live
// on the Rank tab's exports row, to the right of Rebuild, in one horizontal
// row ("The color pickers / text pickers should live here on the scorecard
// itself"), never in Settings -- the button that uses them is the place to
// tune them. The two preview cells read EMU / N64 in their fills: they are
// exactly the legend the paste now writes into worksheet rows 2 and 3.
// The font is a DROPDOWN --
// the app's own `SearchSelect`, never a text field ("This font choice should
// definitely be a DROPDOWN, not this weird text entry"; a native datalist
// drew an unstyled popup pinned to the wrong edge) -- and every name draws
// in its own face, in the list, on the closed trigger and in the preview
// ("we should see its name *in its font*"). No typing means no empty value.
// The colour rows are plain rows, not `<label>`s: a label click activates
// its control, and for a colour input that OPENS the picker, which is how
// clicking the empty space beside "N64 cell" opened one. The swatches are
// circles filled edge to edge, with an outline.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { EMU, N64, PLATFORM_LABELS } from "../platform.js";
import { SearchSelect } from "./searchselect.js";

const html = htm.bind(h);

// Google Sheets' font menu. THE list (round 30): the picker offers exactly
// these, so a name outside it cannot be chosen here. The eight system faces
// need no download; the rest are Google Fonts, loaded once when this
// section mounts so the names render in their faces (offline they fall back
// to the system font, and everything else still works).
export const SHEETS_FONTS = [
  "Amatic SC", "Arial", "Caveat", "Comfortaa", "Comic Sans MS", "Courier New",
  "EB Garamond", "Georgia", "Impact", "Lexend", "Lobster", "Lora",
  "Merriweather", "Montserrat", "Nunito", "Oswald", "Pacifico",
  "Playfair Display", "Roboto", "Roboto Mono", "Roboto Serif", "Spectral",
  "Times New Roman", "Trebuchet MS", "Verdana",
];
const SYSTEM_FONTS = new Set(["Arial", "Comic Sans MS", "Courier New", "Georgia",
                              "Impact", "Times New Roman", "Trebuchet MS", "Verdana"]);
export const GOOGLE_FONTS_HREF = "https://fonts.googleapis.com/css2?"
  + SHEETS_FONTS.filter((font) => !SYSTEM_FONTS.has(font))
    .map((font) => `family=${font.replace(/ /g, "+")}`).join("&")
  + "&display=swap";
const FONTS_LINK_ID = "sheetstyle-google-fonts";

// A CSS `font-family` value for one name, with a generic fallback so a face
// that has not loaded still reads as text of the right kind.
export function fontStack(font) {
  return `'${font}', sans-serif`;
}

function loadGoogleFonts() {
  if (typeof document === "undefined" || document.getElementById(FONTS_LINK_ID)) return;
  const link = document.createElement("link");
  link.id = FONTS_LINK_ID;
  link.rel = "stylesheet";
  link.href = GOOGLE_FONTS_HREF;
  document.head.appendChild(link);
}

const COLOURS = [
  ["emu_fill", `${PLATFORM_LABELS[EMU]} cell`],
  ["n64_fill", `${PLATFORM_LABELS[N64]} cell`],
  ["font_color", "Text"],
];

const FONT_GROUPS = [{ label: null, options: SHEETS_FONTS.map((font) => (
  { value: font, label: font, style: `font-family:${fontStack(font)}` })) }];

export function SheetStyleControls() {
  const [style, setStyle] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    loadGoogleFonts();
    let alive = true;
    getJSON("/api/scorecard/sheet_style")
      .then((body) => { if (alive) { setStyle(body.style); setDefaults(body.defaults); } })
      .catch((err) => alive && setError(err.message));
    return () => { alive = false; };
  }, []);

  async function save(next) {
    setStyle(next);
    setError("");
    try {
      const body = await send("PUT", "/api/scorecard/sheet_style", next);
      setStyle(body.style);
    } catch (err) {
      setError(err.message);
    }
  }

  if (!style) return null;
  const isDefault = !!defaults && Object.keys(defaults)
    .every((key) => style[key] === defaults[key]);
  // The legend cells the paste writes into rows 2 and 3, drawn as they will
  // land: the platform values upper-cased (never spelled here), in their
  // fills, in the chosen text colour and font.
  const legendCell = (platform) => html`<span class="sheetstyle-cell"
      data-platform=${platform}
      style=${`background-color:${platform === N64 ? style.n64_fill : style.emu_fill};`
        + `color:${style.font_color};font-family:${fontStack(style.font_family)}`}
      >${platform.toUpperCase()}</span>`;
  // Round 31: the options first, then the preview they produce -- boxed and
  // captioned ("show the preview AFTER all of the options... clearly label
  // it as 'Preview' above it, in small text, with a surrounding box").
  return html`<div class="sheetstyle" role="group" aria-label="Sheet column colours">
    ${COLOURS.map(([key, caption]) => html`<input type="color" key=${key}
        class=${`sheetstyle-swatch sheetstyle-${key}`}
        title=${caption} aria-label=${caption} value=${style[key]}
        oninput=${(inputEvent) => setStyle({ ...style, [key]: inputEvent.target.value.toUpperCase() })}
        onchange=${(changeEvent) => save({ ...style, [key]: changeEvent.target.value.toUpperCase() })} />`)}
    <span class="sheetstyle-font">
      <${SearchSelect} value=${style.font_family} valueLabel=${style.font_family}
          valueStyle=${`font-family:${fontStack(style.font_family)}`}
          title="Font" groups=${FONT_GROUPS} align="right"
          onChange=${(font) => save({ ...style, font_family: font })} />
    </span>
    <span class="sheetstyle-preview">
      <span class="sheetstyle-preview-label">Preview</span>
      <span class="sheetstyle-preview-cells">${legendCell(EMU)}${legendCell(N64)}</span>
    </span>
    ${isDefault ? "" : html`<button type="button" class="quiet-button sheetstyle-reset"
        title="Back to the default colours and font"
        onclick=${() => defaults && save({ ...defaults })}>Reset</button>`}
    ${error ? html`<span class="settings-note is-bad sheetstyle-error">${error}</span>` : ""}
  </div>`;
}

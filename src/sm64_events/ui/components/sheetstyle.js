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
// The font field is FREE TEXT with the Sheets menu's own fonts as suggestions:
// no primary source for that menu was reachable when this was built
// (2026-09-04), so the list is a convenience, and any name your Sheets menu
// shows works -- Sheets applies a `font-family` it recognises and ignores
// one it does not.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { EMU, N64, PLATFORM_LABELS } from "../platform.js";

const html = htm.bind(h);

// Google Sheets' font menu as of 2026-09 (the "Default (Arial)" entry is
// spelled as its font). A suggestion list, not a whitelist -- see above.
export const SHEETS_FONTS = [
  "Arial", "Amatic SC", "Caveat", "Comfortaa", "Comic Sans MS", "Courier New",
  "EB Garamond", "Georgia", "Impact", "Lexend", "Lobster", "Lora",
  "Merriweather", "Montserrat", "Nunito", "Oswald", "Pacifico",
  "Playfair Display", "Roboto", "Roboto Mono", "Roboto Serif", "Spectral",
  "Times New Roman", "Trebuchet MS", "Verdana",
];

const FIELDS = [
  ["emu_fill", `${PLATFORM_LABELS[EMU]} cell`],
  ["n64_fill", `${PLATFORM_LABELS[N64]} cell`],
  ["font_color", "Text"],
];

export function SheetStyleSection() {
  const [style, setStyle] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
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
  const preview = (platform, text) => html`<span class="sheetstyle-cell"
      data-platform=${platform}
      style=${`background-color:${platform === N64 ? style.n64_fill : style.emu_fill};`
        + `color:${style.font_color};font-family:${style.font_family}`}>${text}</span>`;
  return html`<section class="settings-section sheetstyle">
    <div class="settings-section-head">
      <div>
        <h3>Sheet column colours</h3>
        <p>Pasted cells are coloured by the machine that set the time.</p>
      </div>
    </div>
    <div class="sheetstyle-preview" aria-label="How two pasted cells will look">
      ${preview(EMU, "43.63")}
      ${preview(N64, "45.03")}
    </div>
    ${FIELDS.map(([key, label]) => html`<label class="settings-field" key=${key}>
      <span>${label}</span>
      <input type="color" class=${`sheetstyle-${key}`} value=${style[key]}
          oninput=${(inputEvent) => setStyle({ ...style, [key]: inputEvent.target.value.toUpperCase() })}
          onchange=${(changeEvent) => save({ ...style, [key]: changeEvent.target.value.toUpperCase() })} />
    </label>`)}
    <label class="settings-field">
      <span>Font</span>
      <input type="text" class="sheetstyle-font_family" list="sheetstyle-fonts"
          value=${style.font_family} maxlength="64" spellcheck="false"
          oninput=${(inputEvent) => setStyle({ ...style, font_family: inputEvent.target.value })}
          onchange=${(changeEvent) => save({ ...style, font_family: changeEvent.target.value })} />
      <datalist id="sheetstyle-fonts">
        ${SHEETS_FONTS.map((font) => html`<option value=${font} key=${font} />`)}
      </datalist>
    </label>
    <p class="settings-note">Any font your Sheets menu offers works; the list
      is only a hint. Copy sheet column paints every next copy with these.</p>
    <button type="button" class="quiet-button sheetstyle-reset"
        disabled=${!defaults || FIELDS.every(([key]) => style[key] === defaults[key])
          && style.font_family === (defaults && defaults.font_family)}
        onclick=${() => defaults && save({ ...defaults })}>Reset to defaults</button>
    ${error ? html`<p class="settings-note is-bad">${error}</p>` : ""}
  </section>`;
}

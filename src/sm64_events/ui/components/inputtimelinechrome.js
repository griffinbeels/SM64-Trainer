import { h, Fragment } from "preact";
import htm from "htm";
import { Icon } from "./icons.js";
import { SetupModal } from "./setupmodal.js";
import { EMU } from "../platform.js";
import { heldNames, stickWords } from "./controllerpanel.js";
import { trackFrameOf, timeLabel } from "./inputtimelinemodel.js";

const html = htm.bind(h);

// THE CLIP'S OWN CHECK (`pad_stamp_agreement`), and it is SILENT WHEN IT
// PASSES. The capture layer copies the pad out of RDRAM beside every picture
// it stamps, so extraction can compare that against what the timeline holds
// for the same frame -- exactly, with no pixels involved. It is right on
// every picture of every clip, and a chip saying so is a fact about our
// plumbing rather than about his run: "displaying this to the user is really
// weird lol, worthless information for them" (2026-09-05). Same shape as the
// segment step indicator he retired: the display existed to prove the
// mechanism worked, and it has.
//
// A DISAGREEMENT still draws, because that is the opposite kind of news --
// the panel beside this video is showing a pad the game did not hold, and
// nothing else on the page would say so. Clicking it lists every contradicted
// picture and seeks there.
function screenCheck(agreement, open, toggle) {
  if (!agreement || !agreement.pictures) return null;
  const off = agreement.pictures - agreement.agree;
  if (off === 0) return null;
  const title = "The capture layer copied the pad the game held beside every "
    + `picture it stamped, and the timeline disagrees with it on ${off} of `
    + `${agreement.pictures}. Click to list them.`;
  // A datum on a summary surface is a DOOR to its evidence (his standing
  // rule): the chip opens the list.
  return html`<button type="button"
      class=${`input-screen-check ${open ? "is-open" : ""}`}
      title=${title} aria-expanded=${open}
      onclick=${toggle}>${off} ${off === 1 ? "picture" : "pictures"}${" "}
      disagree with the game</button>`;
}

// Whether this clip's frame map came off the frame-exact capture layer (the
// wrapper plugin, core/capturelayer.py) rather than a reconstruction after
// the fact. A stored association that cannot be verified is a replay-specific
// problem; enabling capture now cannot repair its missing source evidence.
function frameMapNote(frameMapSource, inputAlignment, openSetup) {
  if (inputAlignment?.status === "unverified") {
    return html`<div class="input-frame-map-note">
      <span>Input alignment could not be verified for this replay.</span>
    </div>`;
  }
  if (frameMapSource === "plugin") return null;
  return html`<div class="input-frame-map-note">
    <span>Frame-exact capture is off.</span>
    <button type="button" onclick=${openSetup}>Set up</button>
  </div>`;
}

// The pictures whose stamped pad the timeline does not hold, each as the
// PANEL frame it sits on -- "which 2 disagree?" answered on the surface, and
// a click goes there. `disagreements` rows are
// [slot, frame, track pad, stamped pad], each pad [stick_x, stick_y, buttons].
function padOf(pad, buttons) {
  if (!Array.isArray(pad)) return "--";
  const { vertical, horizontal } = stickWords(pad[0], pad[1]);
  const stick = [vertical, horizontal].filter(Boolean).join(" ") || "neutral";
  const held = heldNames(pad[2], buttons).join(" + ") || "no buttons";
  return `${stick} · ${held}`;
}

function DisagreementList({ agreement, stretches, seek, seekSlot, slotCount, lead, buttons }) {
  const rows = (agreement && agreement.disagreements) || [];
  if (!rows.length) return null;
  return html`<ul class="input-screen-check-list">
    ${rows.map(([slot, raw, tracked, stamped]) => {
      const axis = raw == null ? null : trackFrameOf(raw, stretches);
      const hasSlot = Number.isInteger(slot) && slot >= 0 && slot < slotCount;
      return html`<li key=${slot}>
        <button type="button" class="input-screen-check-row"
            disabled=${seekSlot ? !hasSlot : axis == null}
            onclick=${() => seekSlot ? seekSlot(slot) : axis != null && seek(axis)}>
          <span class="frame">${axis == null ? "outside the track" : `frame ${axis - lead}`}</span>
          <span>game <strong>${padOf(stamped, buttons)}</strong>${" "}·${" "}timeline${" "}<strong>${padOf(tracked, buttons)}</strong></span>
        </button>
      </li>`;
    })}
  </ul>`;
}

export function TimelineStatus({ state, retry }) {
  const data = state.data;
  if (state.phase === "loading") {
    return html`<div class="input-timeline is-loading">Reading inputs…</div>`;
  }
  if (state.phase === "error") {
    return html`<div class="input-timeline is-error">
      Could not read this attempt's inputs: ${state.error}
      <button onclick=${retry}>Retry</button></div>`;
  }
  if (!data.runs.length) {
    return html`<div class="input-timeline is-empty">
      <${Icon} name="feed" size=${18} />
      <div>
        <strong>No inputs recorded for this attempt.</strong>
        <p>It was played before input capture existed, or the trainer was not
           attached to the emulator at the time.</p>
      </div>
    </div>`;
  }

  return null;
}

export function TimelineHeader({ data, refreshError, retry, padAgreement, frameMapSource,
                                 inputAlignment, seek, seekSlot, slotCount,
                                 checkOpen, setCheckOpen, setSetupOpen }) {
  const total = data.frames || 1;
  const lead = data.lead_frames || 0;
  const attemptFrames = data.attempt_frames || (total - lead);
  return html`<${Fragment}>
    <header class="input-timeline-head">
      <div>
        <span class="eyebrow">Inputs</span>
        ${/* The attempt's OWN time -- the number on the row above -- never
              the track's length, which carries the clip's buffers (his
              2026-09-01 report: 19"16 before the clip, 21"30 after, against
              a 0'19"20 row). data-total/data-lead keep the drawn span
              readable by the sweeps. */""}
        <h4 data-total=${total} data-lead=${lead}>${timeLabel(attemptFrames)}${" "}·${" "}${attemptFrames} frames${" "}·${" "}${data.fps} fps</h4>
        ${screenCheck(padAgreement, checkOpen,
                      () => setCheckOpen((open) => !open))}
        ${frameMapNote(frameMapSource, inputAlignment, () => setSetupOpen(true))}
      </div>
    </header>
    ${refreshError && html`<p class="is-error" role="alert">
      Could not refresh the template. Showing the previous comparison.
      <button onclick=${retry}>Retry</button>
    </p>`}
    ${checkOpen && html`<${DisagreementList} agreement=${padAgreement}
        stretches=${data.stretches} seek=${seek} seekSlot=${seekSlot}
        slotCount=${slotCount} lead=${lead} buttons=${data.buttons} />`}

  </${Fragment}>`;
}

export function TemplateNote({ data, offset, total, lead }) {
  const attemptFrames = data.attempt_frames || (total - lead);
  return html`<${Fragment}>
    ${data.template && html`<div class="input-template-note">
      <${Icon} name="bookmark" size=${13} />
      <span>Compared against${" "}<strong>${data.template.name}</strong>${
        data.template.error
          ? html` — <span class="is-error">that template no longer loads:${" "}
              ${data.template.error}</span>`
          : html` — ${data.template.author || "Uncredited"}. ${offset === 0
              ? "Both start at frame 0." : `Template shifted ${offset > 0 ? "+" : ""}${offset} frames.`}
              ${data.template.frames > total - lead
                ? "The template continues beyond this attempt’s visible timeline."
                : data.template.frames < attemptFrames ? "The template ends before your attempt." : ""}`}</span>
    </div>`}

  </${Fragment}>`;
}

export function OverlayControls({ template, data, overlayVisible, toggleOverlay }) {
  return html`<${Fragment}>
    ${template && html`<div class="input-overlay-controls">
      <div class="input-overlay-legend"><span class="is-attempt">Your attempt — solid</span>
        <span class="is-template">Template — dashed / outlined</span></div>
      <details><summary>Template overlay rows</summary>
        <div class="input-overlay-switches">
          ${[["stick", "Stick"], ["actions", "Mario actions"], ["speed", "Speed"],
            ...data.buttons.map(([bit, name]) => [`button:${bit}`, name])]
            .map(([key, label]) => html`<label key=${key}><input type="checkbox"
                checked=${overlayVisible(key)} onchange=${(event) => toggleOverlay(key, event.target.checked)} />
              ${label}</label>`)}
        </div>
        <p class="meta">Applies to all open and future timelines in this browser.</p>
      </details>
    </div>`}

  </${Fragment}>`;
}

export function TimelineSetup({ open, onClose }) {
  return open ? html`<${SetupModal} onClose=${onClose} initialPane=${EMU} />` : null;
}

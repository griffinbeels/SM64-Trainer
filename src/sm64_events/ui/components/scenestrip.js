// src/sm64_events/ui/components/scenestrip.js -- the Ghost Lab's save-state strip (round 9, item 5; lab only).
//
// His words: "I think we need to make it really easy (1) to save to a save state (and see with responsive UIUX
// which save states are saved to / active / etc) so that the user can augment their practice", and on the
// concepts: "maybe we dont have a tab for Save States, and instead it's just a thin strip that shows the
// screenshot previews, under the video player?" So under the play bar sit Usamune's nine slots as they are
// in the running game (GET /api/scene-lab/slots, read only, every SLOTS_POLL.ms while the Ghost Lab shows):
// each tile is its number, a picture of the frame the Ghost Lab put there (a state saved in the game itself
// has no picture: its place's name instead), the flag of the version that made it, ⇄ when the Trainer
// changed it to work in this game's version, and a gold edge with a ▶ for the slot Usamune loaded last; an
// empty slot is its number alone. A click on tile N, or the key N, saves the frame on screen into slot N,
// over what was there, as Usamune's own save does: no question first, and the tile's picture changes. A
// frame that cannot be saved, or a press the game refuses, says why on the line under the tiles, which is
// always there, so nothing moves when it speaks.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { GhostFlag } from "./sceneghosts.js";
import { saveMoment, whyNot } from "./scenemoment.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab/slots";
export const SLOTS_POLL = { ms: 2000 };
export const SLOT_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 9];
const THUMB_WIDTH = 160;

// A small JPEG of the picture on screen (THUMB_WIDTH wide), for the slot it goes into; null without one.
export function thumbnail(canvas) {
  if (!canvas?.width || !canvas.height || typeof document === "undefined") return null;
  const small = document.createElement("canvas");
  small.width = THUMB_WIDTH;
  small.height = Math.round(THUMB_WIDTH * canvas.height / canvas.width);
  const context = small.getContext?.("2d");
  if (!context) return null;
  try {
    context.drawImage(canvas, 0, 0, small.width, small.height);
    return small.toDataURL("image/jpeg", 0.72);
  } catch {
    return null;
  }
}

// The nine slots as the lab last answered (`answer`), the press on its way (`busy`: {slot, shown}) and the
// last press's answer (`said`: {bad, text, build, at}). One per page (SceneLink.slots): the strip and the
// keys share it.
export class SaveSlots {
  constructor({ changed }) {
    Object.assign(this, { changed, answer: null, busy: null, said: null, timer: null, polling: 0 });
  }

  // Asks now, then every SLOTS_POLL.ms until stop(); a new stamp redraws, the same one does not.
  start() {
    const serial = ++this.polling;
    const ask = async () => {
      await this.read();
      if (serial === this.polling) this.timer = setTimeout(ask, SLOTS_POLL.ms);
    };
    clearTimeout(this.timer);
    ask();
  }

  stop() {
    this.polling += 1;
    clearTimeout(this.timer);
  }

  async read() {
    try {
      const answer = await getJSON(ROOT);
      if (answer?.stamp && answer.stamp === this.answer?.stamp) return;
      this.answer = answer;
      this.changed();
    } catch { /* the next ask tries again; the tiles keep what they showed */ }
  }

  // A press of slot `slot` (a tile or its key): the frame on screen into that slot, its picture with it.
  async press(link, slot) {
    if (this.busy || !link.scene || whyNot(link)) return;
    const at = link.frame, shown = at + 1, scene = link.scene, row = scene.frames[at];
    Object.assign(this, { busy: { slot, shown }, said: null });
    this.changed();
    try {
      this.said = { ...await saveMoment(scene, row, shown, slot, thumbnail(link.canvas?.())), at, slot };
    } catch (error) {
      this.said = { bad: true, text: error.message, at, slot };
    }
    this.busy = null;
    this.changed();
    await this.read();
  }

  // A new scene: what was said belonged to the old one.
  forget() {
    this.said = null;
  }
}

// What the line under the tiles says: the press on its way, the last press's answer (while its frame is on
// screen, or during Play), why this frame cannot be saved, or why the slots cannot be read. Empty otherwise.
export function stripLine(link, slots) {
  if (slots.busy) return { bad: false, text: `Saving frame ${slots.busy.shown} in slot ${slots.busy.slot}...` };
  const said = slots.said;
  if (said && (link.playing || said.at === link.frame)) return said;
  const why = whyNot(link);
  if (why) return { bad: false, text: why };
  return slots.answer?.reason ? { bad: false, text: slots.answer.reason } : { bad: false, text: "" };
}

// A tile's words for a reader: what is in it, then what a press does.
function tileLabel(entry, number, loaded) {
  const parts = [`Slot ${number}`];
  if (entry?.state === "empty") parts.push("empty");
  else if (entry?.state === "saved") {
    parts.push(entry.place || "a save state");
    if (entry.version) parts.push(entry.version.toUpperCase());
    if (entry.translated) parts.push("changed to work in this version");
  }
  if (loaded) parts.push("loaded last");
  return `${parts.join(", ")}. Save this frame here (${number}).`;
}

// One slot: number, picture or place, flag, ⇄ and the loaded mark.
function Tile({ entry, number, loaded, busy, disabled, press }) {
  const state = entry?.state || "unknown";
  const classes = ["ghost-lab-slot", `is-${state}`, loaded && "is-loaded", busy && "is-busy"].filter(Boolean);
  return html`<li class=${classes.join(" ")} data-slot=${number}>
    <button type="button" disabled=${disabled} aria-busy=${busy} aria-label=${tileLabel(entry, number, loaded)}
        title=${tileLabel(entry, number, loaded)} onclick=${press}>
      ${state === "saved" && (entry.picture
        ? html`<img class="ghost-lab-slot-picture" src=${entry.picture} alt="" draggable="false" />`
        : html`<span class="ghost-lab-slot-place">${entry.place || "Save state"}</span>`)}
      <kbd>${number}</kbd>
      ${loaded && html`<span class="ghost-lab-slot-loaded" aria-hidden="true">▶</span>`}
      ${state === "saved" && html`<span class="ghost-lab-slot-marks"><${GhostFlag} version=${entry.version} size=${14} />${
        entry.translated ? html`<span class="ghost-lab-slot-crossed" aria-hidden="true">⇄</span>` : null}</span>`}
    </button>
  </li>`;
}

export function SaveStrip({ link }) {
  const slots = link.slots;
  useEffect(() => {
    slots.start();
    return () => slots.stop();
  }, [slots]);
  const answer = slots.answer, why = whyNot(link), line = stripLine(link, slots);
  return html`<div class="ghost-lab-strip" role="group" aria-label="Save states">
    <ol class="ghost-lab-slots">
      ${SLOT_NUMBERS.map((number) => html`<${Tile} key=${number} number=${number}
        entry=${answer?.slots?.find((entry) => entry.slot === number)} loaded=${answer?.loaded === number}
        busy=${slots.busy?.slot === number} disabled=${!!slots.busy || !!why}
        press=${() => slots.press(link, number)} />`)}
    </ol>
    <p class=${`ghost-lab-strip-line${line.bad ? " is-bad" : ""}`} role="status" title=${line.text}>${line.build
      ? html`<${GhostFlag} version=${line.build} size=${14} />` : null}${line.text}</p>
  </div>`;
}

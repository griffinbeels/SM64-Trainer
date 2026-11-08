// src/sm64_events/ui/components/scenestrip.js -- the Ghost Lab's save-state strip (round 9, items 5 and 12;
// lab only).
//
// His words: "I think we need to make it really easy (1) to save to a save state (and see with responsive UIUX
// which save states are saved to / active / etc) so that the user can augment their practice", and on the
// concepts: "maybe we dont have a tab for Save States, and instead it's just a thin strip that shows the
// screenshot previews, under the video player?" So under the play bar sit Usamune's nine slots as they are
// in the running game, asked every SLOTS_POLL.ms while the Ghost Lab shows: each tile is its number, a
// picture of the frame the Ghost Lab put there (a state saved in the game itself has no picture: its place's
// name instead), the flag of the version that made it, ⇄ when the Trainer changed it to work in this game's
// version, and a gold edge with a ▶ for the slot Usamune loaded last; an empty slot is its number alone. A
// click on tile N, or the key N, saves the frame on screen into slot N, over what was there, as Usamune's own
// save does: no question first, and the tile's picture changes. A frame that cannot be saved, or a press the
// game refuses, says why on the line under the tiles, which is always there, so nothing moves when it speaks.
//
// Star save-state sets (item 12), his words: "for every single star, we should be able to have custom save
// states that override the default stage-level save states (if there's a slot set) ... when the user grabs a
// star or selects a star ... load their star-specific save states". GET /api/scene-lab/slot-sets says, for the
// course and star the game is in (`context`, its short label at the line's right end), which set each slot
// came from: a ★ marks a slot from that star's own set, a plain tile one from the course's set, and hover
// names the course save a star's save covers (`under`). A saved tile's small menu moves it to "Only this
// star" or "All stars in this course", or takes it out of its set (the game keeps the slot). A press sends
// the watched run's star with the write, so the save joins that star's set. A slot_sets_changed event in the
// store's feed (link.feed) redraws at once; the poll catches anything else. The lab draws each save's picture
// itself (the recorded camera's, server/scene_moments.py) and serves it at the slot's "picture" URL.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { GhostFlag } from "./sceneghosts.js";
import { saveMoment, whyNot } from "./scenemoment.js";
import { usePop } from "./scenepop.js";

const html = htm.bind(h);
const SETS = "/api/scene-lab/slot-sets";
export const SLOTS_POLL = { ms: 2000 };
export const SLOT_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 9];
export const SLOT_SETS_CHANGED = "slot_sets_changed";
export const NO_GAME = "No game is running, so the slots are unknown.";
export const SET_WORDS = { star: "Only this star", course: "All stars in this course" };

// One slot as every tile reads it: state "empty" | "saved" | "unknown", the set it came from ("star",
// "course" or null), where and which version, ⇄, its picture, loaded last, the course save under it.
const blank = (slot, state = "empty") => ({ slot, state, set: null, place: null, version: null, translated: false,
  picture: null, loaded: false, under: null, id: null });
const placeOf = (state) => ({ place: state.place || state.name || null, version: state.build || null });

// GET /slot-sets's answer as the strip's one shape. Without a game every slot is unknown.
export function fromSlotSets(body) {
  const known = !!body?.game;
  const slots = SLOT_NUMBERS.map((number) => {
    const one = body?.slots?.find((entry) => entry.slot === number);
    if (!known) return blank(number, "unknown");
    if (!one?.state) return blank(number);
    return { slot: number, state: "saved", set: one.set ?? null, ...placeOf(one.state), translated: !!one.translated,
      picture: one.picture || null, loaded: !!one.loaded_last, under: one.under ? placeOf(one.under) : null,
      id: one.state.id ?? null };
  });
  return { game: body?.game?.build ?? null, reason: known ? null : NO_GAME, context: body?.context ?? null,
    stamp: JSON.stringify(body), slots };
}

// The newest slot_sets_changed event in the store's feed (newest first), or null. The same object stays
// in the feed until a newer one arrives, so it is a revision an effect can depend on.
export function newestSlotSetsChange(feed) {
  for (const event of feed || []) {
    if (event && event.type === SLOT_SETS_CHANGED) return event;
  }
  return null;
}

// The watched run's star, for the write: {course_id, star_id}, or nothing without a star.
function starOf(link) {
  const target = link.stageTarget;
  return target?.star_id != null && target.course_id != null ? { course_id: target.course_id, star_id: target.star_id } : {};
}

// The nine slots as the lab last answered (`answer`, the one shape above), the press on its way (`busy`:
// {slot, shown}) and the last press's answer (`said`: {bad, text, build, at}). One per page (SceneLink.slots):
// the strip and the keys share it.
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
      this.take(fromSlotSets(await getJSON(SETS)));
    } catch { /* the next ask tries again; the tiles keep what they showed */ }
  }

  // A new answer (one shape) draws; one with the stamp on screen does not.
  take(answer) {
    if (answer.stamp === this.answer?.stamp) return;
    this.answer = answer;
    this.changed();
  }

  // A slot_sets_changed event's body, pushed by the server.
  pushed(body) {
    this.take(fromSlotSets(body));
  }

  // A press of slot `slot` (a tile or its key): the frame on screen into that slot, with the watched run's
  // star, so the save joins that star's set.
  async press(link, slot) {
    if (this.busy || !link.scene || whyNot(link)) return;
    const at = link.frame, shown = at + 1, scene = link.scene, row = scene.frames[at];
    Object.assign(this, { busy: { slot, shown }, said: null });
    this.changed();
    try {
      this.said = { ...await saveMoment(scene, row, shown, slot, starOf(link)), at, slot };
    } catch (error) {
      this.said = { bad: true, text: error.message, at, slot };
    }
    this.busy = null;
    this.changed();
    await this.read();
  }

  // A tile's menu: slot `slot` into the star's set or the course's ("star" | "course"), or out of `set`.
  // The lab answers with every slot again; a refusal shows on the line.
  move(link, slot, to) {
    return this.change(link, slot, () => send("POST", `${SETS}/${slot}/move`, { to }));
  }

  remove(link, slot, set) {
    return this.change(link, slot, () => send("DELETE", `${SETS}/${slot}?set=${encodeURIComponent(set)}`));
  }

  async change(link, slot, request) {
    try {
      this.take(fromSlotSets(await request()));
    } catch (error) {
      this.said = { bad: true, text: error.message, at: link.frame, slot };
      this.changed();
    }
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

// The course and star the slots follow now, in a few words, or null.
export function contextWords(context) {
  if (!context?.course) return null;
  return context.star ? `${context.course} · ★ ${context.star}` : context.course;
}

// A tile's words for a reader: what is in it, its set, the course save under it, then what a press does.
export function tileLabel(entry, number) {
  const parts = [`Slot ${number}`];
  if (entry?.state === "empty") parts.push("empty");
  else if (entry?.state === "saved") {
    parts.push(entry.place || "a save state");
    if (entry.version) parts.push(entry.version.toUpperCase());
    if (entry.translated) parts.push("changed to work in this version");
    if (entry.set) parts.push(SET_WORDS[entry.set].toLowerCase());
  }
  if (entry?.loaded) parts.push("loaded last");
  const under = entry?.under ? ` Under it is the course save from ${entry.under.place || "this course"}.` : "";
  return `${parts.join(", ")}.${under} Save this frame here (${number}).`;
}

// A saved tile's small menu: which set the slot belongs to (the one it is in is checked), or out of it.
// A choice that cannot run says why on hover.
function SlotMenu({ link, entry, context }) {
  const { open, setOpen, place } = usePop(), slots = link.slots, number = entry.slot;
  const why = { star: context?.star_id == null ? "Pick a star first." : null,
    course: context?.course_id == null ? "Pick a course first." : null };
  const choose = (action) => { setOpen(false); action(); };
  return html`<span class=${`ghost-lab-slot-menu${open ? " is-open" : ""}`} ref=${place}>
    <button type="button" class="icon-button ghost-lab-slot-more" aria-haspopup="menu" aria-expanded=${open}
        aria-label=${`Slot ${number}: which stars use it`} title="Which stars use this slot" onclick=${() => setOpen(!open)}>
      <span aria-hidden="true">⋯</span></button>
    ${open && html`<div class=${`ghost-lab-pop ghost-lab-slot-pop${number > 5 ? " is-end" : ""}`} role="menu"
        aria-label=${`Slot ${number}`}>
      ${["star", "course"].map((set) => html`<button type="button" role="menuitemradio" key=${set}
          aria-checked=${entry.set === set} disabled=${!!why[set]} title=${why[set] || SET_WORDS[set]}
          onclick=${() => choose(() => (entry.set === set ? null : slots.move(link, number, set)))}>
        <span class="ghost-lab-slot-check" aria-hidden="true">${entry.set === set ? "✓" : ""}</span>${SET_WORDS[set]}</button>`)}
      <button type="button" role="menuitem" disabled=${!entry.set}
          title=${entry.set ? "The save state stays in the game." : "It is in no set."}
          onclick=${() => choose(() => slots.remove(link, number, entry.set))}>
        <span class="ghost-lab-slot-check" aria-hidden="true"></span>Remove from set</button>
    </div>`}
  </span>`;
}

// A saved tile's middle and foot: its place's name when it has no picture, then its flag, ★ for the star's
// own set, and ⇄.
function SavedMarks({ entry }) {
  return html`${!entry.picture && html`<span class="ghost-lab-slot-place">${entry.place || "Save state"}</span>`}
    <span class="ghost-lab-slot-marks"><span class="ghost-lab-slot-from"><${GhostFlag} version=${entry.version}
      size=${14} />${entry.set === "star" && html`<span class="ghost-lab-slot-star" aria-hidden="true">★</span>`}</span>${
      entry.translated && html`<span class="ghost-lab-slot-crossed" aria-hidden="true">⇄</span>`}</span>`;
}

// One slot: its picture, number and the loaded mark, then SavedMarks; a saved slot has its menu beside the
// button (never inside it).
function Tile({ link, entry, number, context, busy, disabled, press }) {
  const state = entry?.state || "unknown", saved = state === "saved";
  const classes = ["ghost-lab-slot", `is-${state}`, entry?.loaded && "is-loaded", busy && "is-busy",
    entry?.set && `is-set-${entry.set}`].filter(Boolean);
  const label = tileLabel(entry, number);
  return html`<li class=${classes.join(" ")} data-slot=${number}>
    <button type="button" disabled=${disabled} aria-busy=${busy} aria-label=${label} title=${label} onclick=${press}>
      ${saved && entry.picture && html`<img class="ghost-lab-slot-picture" src=${entry.picture} alt="" draggable="false" />`}
      <span class="ghost-lab-slot-top"><kbd>${number}</kbd>${entry?.loaded
        && html`<span class="ghost-lab-slot-loaded" aria-hidden="true">▶</span>`}</span>
      ${saved && html`<${SavedMarks} entry=${entry} />`}
    </button>
    ${saved && html`<${SlotMenu} link=${link} entry=${entry} context=${context} />`}
  </li>`;
}

export function SaveStrip({ link }) {
  const slots = link.slots, change = newestSlotSetsChange(link.feed);
  useEffect(() => {
    slots.start();
    return () => slots.stop();
  }, [slots]);
  useEffect(() => { if (change?.payload) slots.pushed(change.payload); }, [slots, change]);
  const answer = slots.answer, why = whyNot(link), line = stripLine(link, slots);
  const context = answer?.context ?? null, where = contextWords(context);
  return html`<div class="ghost-lab-strip" role="group" aria-label="Save states">
    <ol class="ghost-lab-slots">
      ${SLOT_NUMBERS.map((number) => html`<${Tile} key=${number} link=${link} number=${number}
        entry=${answer?.slots?.find((entry) => entry.slot === number)} context=${context}
        busy=${slots.busy?.slot === number} disabled=${!!slots.busy || !!why}
        press=${() => slots.press(link, number)} />`)}
    </ol>
    <div class="ghost-lab-strip-foot">
      <p class=${`ghost-lab-strip-line${line.bad ? " is-bad" : ""}`} role="status" title=${line.text}>${line.build
        ? html`<${GhostFlag} version=${line.build} size=${14} />` : null}${line.text}</p>
      ${where && html`<span class="ghost-lab-strip-context" title=${`The slots follow ${where.replace(" · ★ ", ", ")}.`}>${where}</span>`}
    </div>
  </div>`;
}

// src/sm64_events/ui/lineup.js — the Lineup's DOM and motion, framework-free.
//
// The Lineup is the practice card's Ghost Overlay: one centred row of the
// people you race, you first, then faster to the right (Griffin, round 2:
// "our pb on far left, then times get faster as we go to the right"). Each
// seat is a rank cap inside a ring in that ghost's in-game colour, the time,
// and the name. Approved as artifact version 5 (2026-10-16): "This is awesome.
// I think this is it."
//
// WHY THIS FILE OWNS ITS OWN DOM. Every change here is a measured move: a seat
// records where it was, the row reorders, and each seat slides from its old
// place to its new one (the FLIP technique, run on the Web Animations API).
// Preact reconciling the same children mid-slide would fight the transforms,
// so ui/components/lineup.js renders one host element and hands it to
// `createLineup`, which owns everything inside. The component passes it a
// plain model (ui/lineupmodel.js) and this file works out the motion.
//
// TWO MOTION RULES, and nothing else moves:
//   * Same lineup (same star, strategy and scope): people sit in time order, a
//     seat is a rival slot that keeps its colour, and every change slides. A
//     swap, an overtake and a replacement are all that rule. Text swaps are
//     sequential -- the old face leaves before the new one arrives -- the
//     app-wide rule tests/test_ui_text_exchange.py states for exchangeFade.
//   * A different lineup (another star, strategy or scope, or a note): your
//     own seat is the anchor. It stays, glides if the row re-centres, and
//     exchanges only its time, while the others leave together and the new
//     ones arrive left to right.
// Reduced motion turns every move into a short fade.
import { csOfFrame } from "./format.js";

export const MOTION_MS = { out: 120, in: 200, slide: 380, collapse: 280, stagger: 45 };
const EASE = "cubic-bezier(.4,0,.2,1)";
const EASE_IN = "cubic-bezier(.4,0,1,1)";
const EASE_OUT = "cubic-bezier(.2,0,0,1)";
const SVGNS = "http://www.w3.org/2000/svg";
const LOCK = "M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z";
const UNLOCK = "M7 11V8a5 5 0 0 1 9.6-2M6 11h12v9H6z";

function el(tag, cls, attrs = {}) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function glyph(path, size) {
  const svg = document.createElementNS(SVGNS, "svg");
  for (const [k, v] of Object.entries({ width: size, height: size, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", "stroke-width": "2.2", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true" })) svg.setAttribute(k, v);
  const p = document.createElementNS(SVGNS, "path");
  p.setAttribute("d", path);
  svg.append(p);
  return svg;
}

function tick() {
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", "lineup-tick");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Verified account");
  svg.innerHTML = '<circle cx="8" cy="8" r="7.2"/><path d="M4.6 8.3l2.2 2.2 4.6-4.8"/>';
  return svg;
}

// A name owns its whole line and wraps where the handle already divides:
// after _ - . , at a lower-to-upper change, and between letters and digits,
// so "Alexander_Speedruns" breaks as "Alexander_ / Speedruns" and nothing is
// ever cut off (round 2: "names are cut off ... fix this fundamentally").
const BREAK_AFTER = (c, n) => /[_\-.]/.test(c) || (/[a-z]/.test(c) && /[A-Z]/.test(n))
  || (/[A-Za-z]/.test(c) && /[0-9]/.test(n)) || (/[0-9]/.test(c) && /[A-Za-z]/.test(n));
export function breakName(name) {
  const frag = document.createDocumentFragment();
  let chunk = "";
  for (let i = 0; i < name.length; i++) {
    const c = name[i], n = name[i + 1] || "";
    chunk += c;
    if (n && BREAK_AFTER(c, n)) { frag.append(chunk, el("wbr")); chunk = ""; }
  }
  frag.append(chunk);
  return frag;
}

// No time yet sorts as the slowest, so you stay on the left until your first
// finish; a place still loading sorts after everyone, so your seat is first.
const timeOf = p => (p.placeholder ? -Infinity : p.frames == null ? Infinity : p.frames);
export function sortPeople(people) {
  return [...people].sort((a, b) => (timeOf(b) - timeOf(a)) || (a.you ? -1 : b.you ? 1 : 0)
    || String(a.key).localeCompare(String(b.key)));
}

// How much faster the next rival is than you, as the two times are DISPLAYED,
// so the number on the track always equals the difference you can read.
export function gapText(you, next) {
  return `−${((csOfFrame(you.frames) - csOfFrame(next.frames)) / 100).toFixed(2)}`;
}

// Downloading is a class on the seat, not a new face: the ring closes when the ghost is ready.
const signature = p => [p.id, p.frames, p.tier, p.division, !!p.pinned, !!p.verified, p.name, !!p.placeholder].join("|");
const seatsOf = row => [...row.querySelectorAll(":scope > .lineup-seat")];
const fade = (node, from, to, ms, extra = {}) => node.animate([from, to], { duration: ms, ...extra }).finished;

function face(ctx, p, fresh) {
  const f = el("div", "lineup-face");
  const cap = el("span", "lineup-cap");
  if (p.placeholder) {
    f.append(cap, el("span", "lineup-time lineup-bone"), el("span", "lineup-name lineup-bone"));
    return f;
  }
  cap.append(ctx.o.capNode(p, 26));
  const time = el("span", `lineup-time${fresh || p.fresh ? " is-new" : ""}`);
  time.textContent = p.frames == null ? "—" : ctx.o.fmt(p.frames);
  if (p.frames == null) time.setAttribute("aria-label", "No time yet");
  const name = el("span", "lineup-name");
  const text = el("span", "lineup-name-text");
  text.append(p.you ? "You" : breakName(p.name || "Chosen rival"));
  name.append(text);
  if (p.verified) name.append(tick());
  f.append(cap, time, name);
  return f;
}

// A pinned seat wears a lock on its ring: the one thing that says why this
// ghost never moves on its own (round 5: "making it VERY VERY clear why that
// ghost isn't automatically moving"). The lock is the unpin button.
function lock(ctx, p) {
  const b = el("button", "lineup-lock", { type: "button",
    title: `Pinned: ${p.name} stays until you unpin them. Click to let the trainer choose.`,
    "aria-label": `Unpin ${p.name}` });
  // Both glyphs are drawn and CSS shows the open one on hover. Swapping the
  // DOM under the pointer instead set off an endless mouseenter storm in
  // Chromium, and the click never landed.
  const closed = glyph(LOCK, 11), open = glyph(UNLOCK, 11);
  closed.classList.add("lineup-lock-closed");
  open.classList.add("lineup-lock-open");
  b.append(closed, open);
  b.addEventListener("click", event => {
    event.stopPropagation();
    if (ctx.o.onUnpin) ctx.o.onUnpin(ctx.current(b.closest(".lineup-seat").dataset.key));
  });
  return b;
}

function seatLabel(p, fmt) {
  if (p.placeholder) return "Finding a rival";
  const time = p.frames == null ? "no time yet" : fmt(p.frames);
  if (p.you) return `You, ${time}`;
  return `${p.name}, ${time}${p.pinned ? ", pinned" : ""}${p.pending ? ", downloading" : ""}`;
}

function dress(ctx, seat, p, you) {
  const beaten = !p.you && !p.placeholder && Boolean(you) && you.frames != null && p.frames > you.frames;
  seat.style.setProperty("--hue", String(p.hue ?? 210));
  seat.classList.toggle("is-you", !!p.you);
  seat.classList.toggle("no-ghost", !!p.you && !p.ghost);
  seat.classList.toggle("is-pending", !!p.pending);
  seat.classList.toggle("is-placeholder", !!p.placeholder);
  seat.classList.toggle("is-pinned", !!p.pinned);
  seat.classList.toggle("is-beaten", beaten && !p.pinned);
  const badge = seat.querySelector(":scope > .lineup-lock");
  if (p.pinned && !badge) seat.append(lock(ctx, p));
  if (!p.pinned && badge) badge.remove();
  seat.setAttribute("aria-label", seatLabel(p, ctx.o.fmt));
  const swap = seat.querySelector(":scope > .lineup-swap");
  if (swap) { swap.title = `Race someone else instead of ${p.name}`; swap.setAttribute("aria-label", `Swap ${p.name}`); }
}

function seatNode(ctx, p, you) {
  const li = el("li", "lineup-seat");
  li.dataset.key = p.key;
  li.append(el("span", "lineup-mark", { "aria-hidden": "true" }), face(ctx, p));
  if (!p.you && !p.placeholder) {
    const swap = el("button", "lineup-swap", { type: "button" });
    swap.textContent = "⇄";
    swap.addEventListener("click", event => {
      event.stopPropagation();
      if (ctx.o.onSwap) ctx.o.onSwap(ctx.current(li.dataset.key), li);
    });
    li.append(swap);
  }
  dress(ctx, li, p, you);
  return li;
}

// THE STRATEGY BUTTONS share one width (the longest name's) and wrap into
// rows of equal length (round 10: "all of the strategies here fit without it
// being difficult to understand / messy"): six read as one row or as three
// and three, eight as four and four, never five and a straggler. CSS draws
// the equal widths (`.lineup-choices`); this picks the column count from the
// room the note has, whenever that room changes. A ResizeObserver reports
// after layout and before paint, so the first frame is already balanced.
function balanceChoices(note, list) {
  const count = list.children.length;
  const columns = () => {
    // Measured on one unconstrained row, where every button is the widest.
    list.style.setProperty("--lineup-cols", String(count));
    list.style.maxWidth = "none";
    const width = list.firstElementChild.getBoundingClientRect().width;
    list.style.maxWidth = "";
    const gap = parseFloat(getComputedStyle(list).columnGap) || 0;
    const pad = getComputedStyle(note);
    const room = note.clientWidth - parseFloat(pad.paddingLeft) - parseFloat(pad.paddingRight);
    const rows = Math.ceil(count / Math.max(1, Math.floor((room + gap) / (width + gap))));
    list.style.setProperty("--lineup-cols", String(Math.ceil(count / rows)));
  };
  new ResizeObserver(columns).observe(note);
}

// A note stands where missing seats would be: no strategy (with the
// strategies themselves as buttons), nobody yet, offline, an unusable ROM.
function noteNode(ctx, note) {
  const li = el("li", "lineup-note");
  li.append(note.text);
  if (note.choices) {
    li.classList.add("has-choices");
    const list = el("span", "lineup-choices");
    for (const choice of note.choices) {
      const b = el("button", "lineup-choice", { type: "button", title: choice.label });
      b.textContent = choice.label;
      b.addEventListener("click", () => ctx.o.onChoose && ctx.o.onChoose(choice));
      list.append(b);
    }
    li.append(list);
    if (note.choices.length) balanceChoices(li, list);
  } else if (note.action) {
    const go = el("button", "lineup-note-action", { type: "button" });
    go.textContent = note.action;
    go.addEventListener("click", () => ctx.o.onNote && ctx.o.onNote(note));
    li.append(" ", go);
  }
  return li;
}

// The stretch from you to your next rival lights up and carries what you'd cut to pass them.
function markNext(ctx, people) {
  const sorted = sortPeople(people);
  const i = sorted.findIndex(p => p.you);
  const you = sorted[i];
  const next = i >= 0 && you.frames != null ? sorted[i + 1] : null;
  const lit = next && !next.placeholder && next.frames != null ? next.key : null;
  for (const s of seatsOf(ctx.row)) {
    s.classList.toggle("is-next", s.dataset.key === lit);
    if (s.dataset.key === lit) s.dataset.gap = gapText(you, next);
    else delete s.dataset.gap;
  }
}

function paint(ctx, next) {
  const you = next.people.find(p => p.you);
  const items = sortPeople(next.people).map(p => seatNode(ctx, p, you));
  if (next.note) items.push(noteNode(ctx, next.note));
  ctx.row.replaceChildren(...items);
  markNext(ctx, next.people);
  ctx.root.classList.toggle("is-off", !next.on);
}

function easeHeight(ctx, h0, calm) {
  const h1 = ctx.clip.scrollHeight;
  if (!calm && Math.abs(h1 - h0) > 1) {
    ctx.clip.animate([{ height: `${h0}px` }, { height: `${h1}px` }], { duration: MOTION_MS.collapse, easing: EASE });
  }
}

function arriveInOrder(nodes, calm, lead = 0) {
  return Promise.all(nodes.map((k, i) => {
    k.style.opacity = "";
    return fade(k, { opacity: 0, transform: calm ? "none" : "translateY(5px)" }, { opacity: 1, transform: "none" },
      calm ? 140 : 220, { delay: calm ? 0 : lead + i * MOTION_MS.stagger, easing: EASE_OUT, fill: "backwards" });
  }));
}

// A different lineup with no seat of yours on one side: the row leaves, the
// clip eases to the new height, the new row arrives left to right.
async function fadeThrough(ctx, next, calm) {
  const h0 = ctx.clip.getBoundingClientRect().height;
  await fade(ctx.row, { opacity: 1 }, { opacity: 0 }, calm ? 100 : MOTION_MS.out, { easing: EASE_IN, fill: "forwards" });
  paint(ctx, next);
  for (const a of ctx.row.getAnimations()) a.cancel();
  const kids = [...ctx.row.children];
  kids.forEach(k => { k.style.opacity = "0"; });
  easeHeight(ctx, h0, calm);
  await arriveInOrder(kids, calm);
}

function faceOut(node, calm, out) {
  return fade(node, { opacity: 1, transform: "none" }, { opacity: 0, transform: calm ? "none" : "scale(.94)" }, out,
    { easing: EASE_IN, fill: "forwards" });
}

function faceIn(node, calm, delay = 0) {
  node.style.opacity = "";
  return fade(node, { opacity: 0, transform: calm ? "none" : "scale(.94)" }, { opacity: 1, transform: "none" },
    calm ? 140 : MOTION_MS.in, { delay: calm ? 0 : delay, easing: EASE_OUT, fill: "backwards" });
}

// A different lineup with you on both sides: your seat is the anchor.
async function exchange(ctx, prev, next, calm) {
  const mine = ctx.row.querySelector(":scope > .lineup-seat.is-you");
  const was = prev.people.find(p => p.you), now = next.people.find(p => p.you);
  if (!mine || !was || !now) return fadeThrough(ctx, next, calm);
  const out = calm ? 100 : MOTION_MS.out;
  const h0 = ctx.clip.getBoundingClientRect().height;
  const x0 = mine.getBoundingClientRect().left;
  const changes = signature(was) !== signature(now) || !!was.ghost !== !!now.ghost;
  let myFace = mine.querySelector(".lineup-face");
  const leaving = [...ctx.row.children].filter(k => k !== mine);
  await Promise.all([...leaving.map(k => fade(k, { opacity: 1 }, { opacity: 0 }, out, { easing: EASE_IN, fill: "forwards" })),
    ...(changes ? [faceOut(myFace, calm, out)] : [])]);
  const items = sortPeople(next.people).map(p => (p.you ? mine : seatNode(ctx, p, now)));
  if (next.note) items.push(noteNode(ctx, next.note));
  dress(ctx, mine, now, now);
  if (changes) { const f = face(ctx, now, now.fresh); f.style.opacity = "0"; myFace.replaceWith(f); myFace = f; }
  const arriving = items.filter(k => k !== mine);
  arriving.forEach(k => { k.style.opacity = "0"; });
  ctx.row.replaceChildren(...items);
  markNext(ctx, next.people);
  easeHeight(ctx, h0, calm);
  const dx = x0 - mine.getBoundingClientRect().left;
  const glide = !calm && Math.abs(dx) > .5;
  if (glide) mine.animate([{ transform: `translateX(${dx}px)` }, { transform: "none" }], { duration: MOTION_MS.slide, easing: EASE });
  await Promise.all([...(changes ? [faceIn(myFace, calm)] : []),
    arriveInOrder(arriving, calm, glide ? Math.round(MOTION_MS.slide * .35) : 0)]);
}

// Same lineup, step 2: the same person with a new value (your PB) updates in
// place before anyone moves; a new person stays hidden until their seat lands.
async function refaceChanged(ctx, changed, byKey, before, calm) {
  const newcomers = [], updated = [];
  for (const p of changed) {
    const was = before.get(p.key);
    const f = face(ctx, p, p.you && p.frames != null && (was.frames == null || was.frames > p.frames));
    f.style.opacity = "0";
    byKey.get(p.key).querySelector(".lineup-face").replaceWith(f);
    (was.id === p.id ? updated : newcomers).push(f);
  }
  await Promise.all(updated.map(f => faceIn(f, calm)));
  return newcomers;
}

// Same lineup, step 3: record where every seat is, reorder, then slide each
// from its old place. A seat moving right (an overtake) passes over the ones
// it leaves behind.
function reorderAndSlide(ctx, { sorted, leaving, entering, byKey, you, newcomers }, calm) {
  const first = new Map(seatsOf(ctx.row).map(s => [s.dataset.key, s.getBoundingClientRect().left]));
  leaving.forEach(k => byKey.get(k).remove());
  for (const p of entering) { const s = seatNode(ctx, p, you); s.style.opacity = "0"; byKey.set(p.key, s); newcomers.push(s); }
  for (const p of sorted) { const s = byKey.get(p.key); dress(ctx, s, p, you); ctx.row.append(s); }
  const note = ctx.row.querySelector(":scope > .lineup-note");
  if (note) ctx.row.append(note);
  const slides = [];
  for (const s of seatsOf(ctx.row)) {
    const dx = (first.get(s.dataset.key) ?? NaN) - s.getBoundingClientRect().left;
    if (calm || !(Math.abs(dx) >= .5)) continue;
    s.style.zIndex = dx < 0 ? "3" : "";
    slides.push(s.animate([{ transform: `translateX(${dx}px)` }, { transform: "translateX(0)" }], { duration: MOTION_MS.slide, easing: EASE })
      .finished.then(() => { s.style.zIndex = ""; }));
  }
  const moved = slides.length > 0 || leaving.length > 0;
  if (moved) { ctx.row.classList.add("is-moving"); Promise.all(slides).then(() => ctx.row.classList.remove("is-moving")); }
  return { slides, moved };
}

// Same lineup: the slide rule. The lit stretch and its number step aside while
// people change and come back once everyone has landed.
async function changeInPlace(ctx, prev, next, calm) {
  const you = next.people.find(p => p.you);
  const byKey = new Map(seatsOf(ctx.row).map(s => [s.dataset.key, s]));
  const before = new Map(prev.people.map(p => [p.key, p]));
  const sorted = sortPeople(next.people);
  const changed = sorted.filter(p => before.has(p.key) && signature(before.get(p.key)) !== signature(p));
  const leaving = [...byKey.keys()].filter(k => !sorted.some(p => p.key === k));
  const entering = sorted.filter(p => !byKey.has(p.key));
  for (const p of sorted) if (byKey.has(p.key)) dress(ctx, byKey.get(p.key), p, you);
  if (!changed.length && !leaving.length && !entering.length) { markNext(ctx, next.people); return; }
  ctx.row.classList.add("is-settling");
  const out = calm ? 100 : MOTION_MS.out;
  await Promise.all([...changed.map(p => faceOut(byKey.get(p.key).querySelector(".lineup-face"), calm, out)),
    ...leaving.map(k => fade(byKey.get(k), { opacity: 1 }, { opacity: 0 }, out, { fill: "forwards" }))]);
  const newcomers = await refaceChanged(ctx, changed, byKey, before, calm);
  const { slides, moved } = reorderAndSlide(ctx, { sorted, leaving, entering, byKey, you, newcomers }, calm);
  // Step 4: newcomers appear as their seat lands, left to right.
  const order = node => [...ctx.row.children].indexOf(node.closest(".lineup-seat"));
  newcomers.sort((a, b) => order(a) - order(b));
  const lead = moved ? Math.round(MOTION_MS.slide * .55) : 0;
  await Promise.all(newcomers.map((node, i) => faceIn(node, calm, lead + i * MOTION_MS.stagger)));
  await Promise.all(slides);
  markNext(ctx, next.people);
  ctx.row.classList.remove("is-settling");
}

// Turning ghosts on unfolds the row; its seats arrive left to right.
function arrive(ctx) {
  [...ctx.row.children].forEach((s, i) => s.animate([{ opacity: 0, transform: "translateY(5px)" }, { opacity: 1, transform: "none" }],
    { duration: 220, delay: 90 + i * MOTION_MS.stagger, easing: EASE_OUT, fill: "backwards" }));
}

const noteKey = n => (n ? `${n.text}|${(n.choices || []).map(c => c.label).join(",")}` : "");

/**
 * Mount the Lineup into `root`. `o` supplies what this file cannot know:
 * capNode(person, size) -> Node, fmt(frames) -> text, reduced() -> bool, and
 * the gestures onSwap(person, seatElement), onUnpin(person), onChoose(choice),
 * onNote(note). Returns {set(model, {animate}), state, row}.
 */
export function createLineup(root, o) {
  root.classList.add("lineup");
  const body = el("div", "lineup-body");
  const clip = el("div", "lineup-clip");
  const row = el("ol", "lineup-row", { "aria-label": "Ghosts you race, slowest to fastest" });
  clip.append(row);
  body.append(clip);
  root.replaceChildren(body);
  let state = { on: true, people: [], lineup: null, note: null };
  const ctx = { o, root, clip, row, current: key => state.people.find(p => p.key === key) };
  const reduced = () => (o.reduced ? o.reduced() : matchMedia("(prefers-reduced-motion: reduce)").matches);

  async function set(next, { animate = true } = {}) {
    const prev = state;
    state = next;
    if (!animate || (!prev.on && !next.on) || !prev.lineup) { paint(ctx, next); return; }
    const calm = reduced();
    const switched = next.on !== prev.on;
    if (next.lineup !== prev.lineup || noteKey(next.note) !== noteKey(prev.note)) {
      if (!switched) return exchange(ctx, prev, next, calm);
      paint(ctx, next);
    } else if (switched) {
      root.classList.toggle("is-off", !next.on);
      await changeInPlace(ctx, prev, next, true);
    } else {
      return changeInPlace(ctx, prev, next, calm);
    }
    if (next.on && !calm) arrive(ctx);
  }

  return { set, get state() { return state; }, row, root };
}

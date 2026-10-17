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
// plain model (ui/lineupmodel.js) and the core works out the motion.
//
// TWO MOTION RULES, and nothing else moves:
//   * Same lineup (same star, strategy and scope): people sit in time order, a
//     seat is a rival slot that keeps its colour, and every change slides. A
//     swap, an overtake and a replacement are all that rule. Text swaps are
//     sequential -- the old face leaves before the new one arrives -- the
//     app-wide rule tests/test_ui_text_exchange.py states for exchangeFade.
//   * A different lineup (another star, strategy or scope, loading resolving,
//     a note): your own seat is the anchor. It stays, glides if the row
//     re-centres, and exchanges only its time, while the others leave
//     together and the new ones arrive left to right.
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
export function breakName(name) {
  const frag = document.createDocumentFragment();
  let chunk = "";
  for (let i = 0; i < name.length; i++) {
    const c = name[i], n = name[i + 1] || "";
    chunk += c;
    const split = n && (/[_\-.]/.test(c) || (/[a-z]/.test(c) && /[A-Z]/.test(n))
      || (/[A-Za-z]/.test(c) && /[0-9]/.test(n)) || (/[0-9]/.test(c) && /[A-Za-z]/.test(n)));
    if (split) { frag.append(chunk, el("wbr")); chunk = ""; }
  }
  frag.append(chunk);
  return frag;
}

// No time yet sorts as the slowest, so you stay on the left until your first finish.
const timeOf = p => (p.frames == null ? Infinity : p.frames);
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

/**
 * Mount the Lineup into `root`. `o` supplies what the core cannot know:
 * capNode(person, size) -> Node, fmt(frames) -> text, reduced() -> bool, and
 * the gestures onSwap(person, seatElement), onUnpin(person), onChoose(strategy),
 * onNote(note). Returns {set(model, {animate}), state, row}.
 */
export function createLineup(root, o) {
  const reduced = () => (o.reduced ? o.reduced() : matchMedia("(prefers-reduced-motion: reduce)").matches);
  root.classList.add("lineup");
  root.replaceChildren();
  const body = el("div", "lineup-body");
  const clip = el("div", "lineup-clip");
  const row = el("ol", "lineup-row", { "aria-label": "Ghosts you race, slowest to fastest" });
  clip.append(row);
  body.append(clip);
  root.append(body);
  let state = { on: true, people: [], lineup: null, note: null };

  function face(p, fresh) {
    const f = el("div", "lineup-face");
    const cap = el("span", "lineup-cap");
    if (p.placeholder) {
      f.append(cap, el("span", "lineup-time lineup-bone"), el("span", "lineup-name lineup-bone"));
      return f;
    }
    cap.append(o.capNode(p, 26));
    const time = el("span", "lineup-time" + (fresh || p.fresh ? " is-new" : ""));
    time.textContent = p.frames == null ? "—" : o.fmt(p.frames);
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
  function lock(p) {
    const b = el("button", "lineup-lock", { type: "button",
      title: `Pinned: ${p.name} stays until you unpin them. Click to let the trainer choose.`,
      "aria-label": `Unpin ${p.name}` });
    b.append(glyph(LOCK, 11));
    b.addEventListener("mouseenter", () => b.replaceChildren(glyph(UNLOCK, 11)));
    b.addEventListener("mouseleave", () => b.replaceChildren(glyph(LOCK, 11)));
    b.addEventListener("click", event => { event.stopPropagation(); o.onUnpin && o.onUnpin(current(b.closest(".lineup-seat").dataset.key)); });
    return b;
  }

  function dress(seat, p, you) {
    seat.style.setProperty("--hue", p.hue == null ? "210" : String(p.hue));
    seat.classList.toggle("is-you", !!p.you);
    seat.classList.toggle("no-ghost", !!p.you && !p.ghost);
    seat.classList.toggle("is-pending", !!p.pending);
    seat.classList.toggle("is-placeholder", !!p.placeholder);
    seat.classList.toggle("is-pinned", !!p.pinned);
    const beaten = !p.you && !p.placeholder && you && you.frames != null && p.frames > you.frames;
    seat.classList.toggle("is-beaten", beaten && !p.pinned);
    let badge = seat.querySelector(":scope > .lineup-lock");
    if (p.pinned && !badge) seat.append(badge = lock(p));
    if (!p.pinned && badge) badge.remove();
    if (p.placeholder) { seat.setAttribute("aria-label", "Finding a rival"); return; }
    const time = p.frames == null ? "no time yet" : o.fmt(p.frames);
    seat.setAttribute("aria-label", p.you ? `You, ${time}`
      : `${p.name}, ${time}${p.pinned ? ", pinned" : ""}${p.pending ? ", downloading" : ""}`);
    const swap = seat.querySelector(":scope > .lineup-swap");
    if (swap) { swap.title = `Race someone else instead of ${p.name}`; swap.setAttribute("aria-label", `Swap ${p.name}`); }
  }

  function seat(p, you) {
    const li = el("li", "lineup-seat");
    li.dataset.key = p.key;
    li.append(el("span", "lineup-mark", { "aria-hidden": "true" }), face(p));
    if (!p.you && !p.placeholder) {
      const swap = el("button", "lineup-swap", { type: "button" });
      swap.textContent = "⇄";
      swap.addEventListener("click", event => { event.stopPropagation(); o.onSwap && o.onSwap(current(li.dataset.key), li); });
      li.append(swap);
    }
    dress(li, p, you);
    return li;
  }

  const current = key => state.people.find(p => p.key === key);
  const seats = () => new Map([...row.querySelectorAll(":scope > .lineup-seat")].map(s => [s.dataset.key, s]));

  // A note stands where missing seats would be: no strategy (with the
  // strategies themselves as buttons), nobody yet, offline, an unusable ROM.
  function noteNode(note) {
    const li = el("li", "lineup-note");
    if (note.choices) {
      li.classList.add("has-choices");
      li.append(note.text);
      const list = el("span", "lineup-choices");
      for (const c of note.choices) {
        const b = el("button", "lineup-choice", { type: "button" });
        b.textContent = c.label;
        b.addEventListener("click", () => o.onChoose && o.onChoose(c));
        list.append(b);
      }
      li.append(list);
      return li;
    }
    li.append(note.text);
    if (note.action) {
      const go = el("button", "lineup-note-action", { type: "button" });
      go.textContent = note.action;
      go.addEventListener("click", () => o.onNote && o.onNote(note));
      li.append(" ", go);
    }
    return li;
  }

  // The stretch from you to your next rival lights up and carries what you'd cut to pass them.
  function markNext(people) {
    const sorted = sortPeople(people);
    const i = sorted.findIndex(p => p.you);
    const you = sorted[i];
    const next = i >= 0 && you.frames != null ? sorted[i + 1] : null;
    for (const s of row.querySelectorAll(":scope > .lineup-seat")) {
      const isNext = !!next && !next.placeholder && next.frames != null && s.dataset.key === next.key;
      s.classList.toggle("is-next", isNext);
      if (isNext) s.dataset.gap = gapText(you, next);
      else delete s.dataset.gap;
    }
  }

  function paint(next) {
    const you = next.people.find(p => p.you);
    const items = sortPeople(next.people).map(p => seat(p, you));
    if (next.note) items.push(noteNode(next.note));
    row.replaceChildren(...items);
    markNext(next.people);
    root.classList.toggle("is-off", !next.on);
  }

  // A different lineup with no seat of yours on one side: the row leaves, the
  // clip eases to the new height, the new row arrives left to right.
  async function fadeThrough(next, calm) {
    const h0 = clip.getBoundingClientRect().height;
    await row.animate([{ opacity: 1 }, { opacity: 0 }], { duration: calm ? 100 : MOTION_MS.out, easing: EASE_IN, fill: "forwards" }).finished;
    paint(next);
    const h1 = clip.scrollHeight;
    for (const a of row.getAnimations()) a.cancel();
    const kids = [...row.children];
    kids.forEach(k => { k.style.opacity = "0"; });
    if (!calm && Math.abs(h1 - h0) > 1) clip.animate([{ height: `${h0}px` }, { height: `${h1}px` }], { duration: MOTION_MS.collapse, easing: EASE });
    await Promise.all(kids.map((k, i) => {
      k.style.opacity = "";
      return k.animate([{ opacity: 0, transform: calm ? "none" : "translateY(5px)" }, { opacity: 1, transform: "none" }],
        { duration: calm ? 140 : 220, delay: calm ? 0 : i * MOTION_MS.stagger, easing: EASE_OUT, fill: "backwards" }).finished;
    }));
  }

  // A different lineup with you on both sides: your seat is the anchor.
  async function exchange(prev, next, calm) {
    const mine = row.querySelector(":scope > .lineup-seat.is-you");
    const was = prev.people.find(p => p.you), now = next.people.find(p => p.you);
    if (!mine || !was || !now) return fadeThrough(next, calm);
    const out = calm ? 100 : MOTION_MS.out;
    const h0 = clip.getBoundingClientRect().height;
    const x0 = mine.getBoundingClientRect().left;
    const changes = signature(was) !== signature(now) || !!was.ghost !== !!now.ghost;
    let myFace = mine.querySelector(".lineup-face");
    const leaving = [...row.children].filter(k => k !== mine);
    await Promise.all([
      ...leaving.map(k => k.animate([{ opacity: 1 }, { opacity: 0 }], { duration: out, easing: EASE_IN, fill: "forwards" }).finished),
      ...(changes ? [myFace.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: calm ? "none" : "scale(.94)" }],
        { duration: out, easing: EASE_IN, fill: "forwards" }).finished] : []),
    ]);
    const items = sortPeople(next.people).map(p => (p.you ? mine : seat(p, now)));
    if (next.note) items.push(noteNode(next.note));
    dress(mine, now, now);
    if (changes) { const f = face(now, now.fresh); f.style.opacity = "0"; myFace.replaceWith(f); myFace = f; }
    const arriving = items.filter(k => k !== mine);
    arriving.forEach(k => { k.style.opacity = "0"; });
    row.replaceChildren(...items);
    markNext(next.people);
    const h1 = clip.scrollHeight;
    if (!calm && Math.abs(h1 - h0) > 1) clip.animate([{ height: `${h0}px` }, { height: `${h1}px` }], { duration: MOTION_MS.collapse, easing: EASE });
    const dx = x0 - mine.getBoundingClientRect().left;
    const glide = !calm && Math.abs(dx) > .5;
    if (glide) mine.animate([{ transform: `translateX(${dx}px)` }, { transform: "none" }], { duration: MOTION_MS.slide, easing: EASE });
    const lead = glide ? Math.round(MOTION_MS.slide * .35) : 0;
    await Promise.all([
      ...(changes ? [(() => {
        myFace.style.opacity = "";
        return myFace.animate([{ opacity: 0, transform: calm ? "none" : "scale(.94)" }, { opacity: 1, transform: "none" }],
          { duration: calm ? 140 : MOTION_MS.in, easing: EASE_OUT, fill: "backwards" }).finished;
      })()] : []),
      ...arriving.map((k, i) => {
        k.style.opacity = "";
        return k.animate([{ opacity: 0, transform: calm ? "none" : "translateY(5px)" }, { opacity: 1, transform: "none" }],
          { duration: calm ? 140 : 220, delay: calm ? 0 : lead + i * MOTION_MS.stagger, easing: EASE_OUT, fill: "backwards" }).finished;
      }),
    ]);
  }

  async function set(next, { animate = true } = {}) {
    const prev = state;
    state = next;
    if (!animate || (!prev.on && !next.on) || !prev.lineup) { paint(next); return; }
    const calm = reduced();
    const noteKey = n => (n ? `${n.text}|${(n.choices || []).map(c => c.label).join(",")}` : "");
    if (next.lineup !== prev.lineup || noteKey(next.note) !== noteKey(prev.note)) {
      if (next.on !== prev.on) {
        paint(next);
        if (next.on && !calm) arrive();
        return;
      }
      return exchange(prev, next, calm);
    }
    const you = next.people.find(p => p.you);
    const byKey = seats();
    const before = new Map(prev.people.map(p => [p.key, p]));
    const sorted = sortPeople(next.people);
    const changed = sorted.filter(p => before.has(p.key) && signature(before.get(p.key)) !== signature(p));
    const leaving = [...byKey.keys()].filter(k => !sorted.some(p => p.key === k));
    const entering = sorted.filter(p => !byKey.has(p.key));
    for (const p of sorted) if (byKey.has(p.key)) dress(byKey.get(p.key), p, you);
    if (next.on !== prev.on) {
      root.classList.toggle("is-off", !next.on);
      if (next.on && !calm) arrive();
      if (!changed.length && !leaving.length && !entering.length) { markNext(next.people); return; }
    }
    if (!changed.length && !leaving.length && !entering.length) { markNext(next.people); return; }
    // The lit stretch and its number step aside while people change and come
    // back once everyone has landed, so they never point at the wrong pair.
    row.classList.add("is-settling");
    const out = calm ? 100 : MOTION_MS.out;
    // 1. The outgoing content leaves first.
    await Promise.all([
      ...changed.map(p => byKey.get(p.key).querySelector(".lineup-face").animate(
        [{ opacity: 1, transform: "none" }, { opacity: 0, transform: calm ? "none" : "scale(.94)" }],
        { duration: out, easing: EASE_IN, fill: "forwards" }).finished),
      ...leaving.map(k => byKey.get(k).animate([{ opacity: 1 }, { opacity: 0 }], { duration: out, fill: "forwards" }).finished),
    ]);
    // 2. The same person with a new value (your PB) updates in place before
    //    anyone moves; a new person stays hidden until their seat lands.
    const newcomers = [], updated = [];
    for (const p of changed) {
      const s = byKey.get(p.key);
      const same = before.get(p.key).id === p.id;
      const was = before.get(p.key).frames;
      const f = face(p, p.you && p.frames != null && (was == null || was > p.frames));
      f.style.opacity = "0";
      s.querySelector(".lineup-face").replaceWith(f);
      (same ? updated : newcomers).push(f);
    }
    await Promise.all(updated.map(f => {
      f.style.opacity = "";
      return f.animate([{ opacity: 0, transform: calm ? "none" : "scale(.94)" }, { opacity: 1, transform: "none" }],
        { duration: calm ? 140 : MOTION_MS.in, easing: EASE_OUT, fill: "backwards" }).finished;
    }));
    // 3. Record where every seat is, reorder, then slide each from its old
    //    place. A seat moving right (an overtake) passes over the ones it leaves.
    const first = new Map([...row.querySelectorAll(":scope > .lineup-seat")].map(s => [s.dataset.key, s.getBoundingClientRect().left]));
    leaving.forEach(k => byKey.get(k).remove());
    for (const p of entering) { const s = seat(p, you); s.style.opacity = "0"; byKey.set(p.key, s); newcomers.push(s); }
    for (const p of sorted) { const s = byKey.get(p.key); dress(s, p, you); row.append(s); }
    const note = row.querySelector(":scope > .lineup-note");
    if (note) row.append(note);
    const slides = [];
    for (const s of row.querySelectorAll(":scope > .lineup-seat")) {
      const was = first.get(s.dataset.key);
      if (was == null || calm) continue;
      const dx = was - s.getBoundingClientRect().left;
      if (Math.abs(dx) < .5) continue;
      s.style.zIndex = dx < 0 ? "3" : "";
      slides.push(s.animate([{ transform: `translateX(${dx}px)` }, { transform: "translateX(0)" }], { duration: MOTION_MS.slide, easing: EASE })
        .finished.then(() => { s.style.zIndex = ""; }));
    }
    const moved = slides.length > 0 || leaving.length > 0;
    if (moved) { row.classList.add("is-moving"); Promise.all(slides).then(() => row.classList.remove("is-moving")); }
    // 4. Newcomers appear as their seat lands, left to right.
    const order = node => [...row.children].indexOf(node.closest(".lineup-seat"));
    newcomers.sort((a, b) => order(a) - order(b));
    await Promise.all(newcomers.map((node, i) => {
      node.style.opacity = "";
      return node.animate([{ opacity: 0, transform: calm ? "none" : "scale(.94)" }, { opacity: 1, transform: "none" }],
        { duration: calm ? 140 : MOTION_MS.in, delay: calm ? 0 : (moved ? Math.round(MOTION_MS.slide * .55) : 0) + i * MOTION_MS.stagger,
          easing: EASE_OUT, fill: "backwards" }).finished;
    }));
    await Promise.all(slides);
    markNext(next.people);
    row.classList.remove("is-settling");
  }

  // Turning ghosts on unfolds the row; its seats arrive left to right.
  function arrive() {
    [...row.children].forEach((s, i) => s.animate([{ opacity: 0, transform: "translateY(5px)" }, { opacity: 1, transform: "none" }],
      { duration: 220, delay: 90 + i * MOTION_MS.stagger, easing: EASE_OUT, fill: "backwards" }));
  }

  return { set, get state() { return state; }, row, root };
}

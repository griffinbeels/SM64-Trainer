// src/sm64_events/ui/tunefeed.js — the feed & disclosure inspector, served at
// /ui/tunefeed.html.
//
// Griffin, 2026-08-05: "I want to figure out how to animate all of this...
// I'm being intentionally vague because I'm not too sure what would look best
// here." That last clause is the whole reason this page exists rather than a
// commit full of durations I picked: "like how I would work with an Inspector
// in Godot... get it to how it feels good for ME, and then tell you what we
// want to codify" (2026-07-27).
//
// WHAT IS REAL HERE, stated plainly because a rig that quietly judges
// something other than the app is the one way this exercise becomes
// worthless: the MOTION is entirely shipped code -- `useFeedMotion`
// (components/feedmotion.js), `Disclose` (components/collapsible.js), the
// plans in `ui/disclosure.js` and every number in `ui/feedtuning.js`. What is
// a REPLICA is the card CHROME: real class names from the app's own
// stylesheet, but not `LogCard` itself, which takes the live store rather than
// props and cannot be mounted outside the app. That is the same standing gap
// `tuneselector.js` records for its own card chrome, and the same fix if it
// ever bites: make the component presentational.
//
// Every control is generated from ui/feedtuning.js's registry, so a new
// tunable appears here with no edit to this file. SAVE POSTs to
// /api/tuning/feed, which rewrites that registry's `value:` fields -- what
// lands in `git diff` afterwards IS the tuning, ready to commit.
import { h, render } from "preact";
import { useMemo, useRef, useState, useEffect } from "preact/hooks";
import htm from "htm";

import { Disclose } from "/ui/components/collapsible.js";
import { useFeedMotion } from "/ui/components/feedmotion.js";
import { FEED_TUNABLES, FEED_GROUPS, FEED_DEFAULTS,
         withFeedDefaults, setFeedTuning } from "/ui/feedtuning.js";
import { feedSettleMs } from "/ui/disclosure.js";
import { ControlGroups } from "/ui/tunecontrols.js";
import { prefersReducedMotion } from "/ui/useTween.js";

const html = htm.bind(h);
const STORE_KEY = "sm64.feedTuneDraft";

// Placeholder cards shaped like the real ones. Names are deliberately
// arbitrary and NO TEST MAY ASSERT THEM -- the shipped-default rule this
// codebase enforces for every tuning page.
const SEED = [
  { key: "star:2:2", context: "Whomp's Fortress", name: "Shoot into the Wild Blue" },
  { key: "star:8:1", context: "Shifting Sand Land", name: "In the Talons of the Big Bird" },
  { key: "segment:12", context: "Segment", name: "Bowser 2 → BitS" },
  { key: "star:7:4", context: "Hazy Maze Cave", name: "Watch for Rolling Rocks" },
];

const ARRIVALS = [
  { key: "star:5:1", context: "Cool, Cool Mountain", name: "Slip Slidin' Away" },
  { key: "segment:67", context: "Segment", name: "BitDW — 8 Red Coins → Pipe" },
  { key: "star:23:0", context: "Dire, Dire Docks", name: "Board Bowser's Sub" },
];

const changedFromShipped = (values) =>
  Object.fromEntries(Object.entries(withFeedDefaults(values))
    .filter(([key, value]) => value !== FEED_DEFAULTS[key]));

function Card({ card, open, onToggle }) {
  return html`<section data-feed-key=${card.key}
      class=${`log-card ${open ? "" : "is-closed"}`}>
    <div class="log-card-head">
      <div class="log-card-identity">
        <span class="log-card-name">
          <span class="log-card-context">${card.context}</span>
          <b>${card.name}</b>
        </span>
      </div>
      <button type="button" class="log-card-fold" onclick=${onToggle}
          aria-expanded=${open ? "true" : "false"}
          title=${`${open ? "Collapse" : "Expand"} ${card.name}`}>▾</button>
    </div>
    <${Disclose} open=${open} className="log-card-disclose">
      <div class="log-card-body">
        <table class="attempt-table"><tbody>
          ${[1, 2, 3, 4, 5].map((row) => html`<tr key=${row}>
            <td>#${row}</td><td class="attempt-result">✓ 0'11"${30 + row}</td>
            <td>Standard</td></tr>`)}
        </tbody></table>
      </div>
    <//>
  </section>`;
}

function Inspector() {
  const [values, setValues] = useState(() => {
    try { return withFeedDefaults(JSON.parse(localStorage.getItem(STORE_KEY))); }
    catch (error) { return withFeedDefaults(null); }
  });
  const [cards, setCards] = useState(SEED);
  const [openKey, setOpenKey] = useState(null);
  const [nextArrival, setNextArrival] = useState(0);
  const [status, setStatus] = useState(null);
  const listRef = useRef(null);

  // The ACTIVE slot is what the shipped components read, so the WIRING layer
  // sets it -- never a consumer, which would make it undrivable from here.
  useEffect(() => {
    setFeedTuning(values);
    localStorage.setItem(STORE_KEY, JSON.stringify(values));
  }, [values]);

  useFeedMotion(listRef, cards.map((card) => card.key));

  const set = (key, value) => setValues((prev) => ({ ...prev, [key]: value }));
  const changed = useMemo(() => changedFromShipped(values), [values]);
  const changedKeys = Object.keys(changed);

  // The case the whole mechanism exists for: one card arrives at the TOP and
  // every card below it is displaced. If the push does not read as caused by
  // the arrival here, it does not in the app -- same hook, same numbers.
  function pushOne() {
    const arrival = ARRIVALS[nextArrival % ARRIVALS.length];
    setNextArrival((n) => n + 1);
    setCards((prev) => [{ ...arrival, key: `${arrival.key}#${nextArrival}` },
                        ...prev].slice(0, 6));
  }

  async function saveToRepo() {
    setStatus({ kind: "busy", text: "Saving..." });
    try {
      const response = await fetch("/api/tuning/feed", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      localStorage.removeItem(STORE_KEY);
      setStatus({ kind: "ok",
        text: `Saved ${payload.written} value(s) into feedtuning.js. Reloading...` });
      setTimeout(() => location.reload(), 700);
    } catch (error) {
      setStatus({ kind: "bad", text: String(error.message || error) });
    }
  }

  // WHAT WILL ACTUALLY PLAY, live as the sliders move. The step the
  // tuning-demo skill calls non-optional: a control's number is not always the
  // number that runs, and the settle time is the one figure that answers "how
  // long is this gesture" without re-deriving the stagger by hand.
  const settle = feedSettleMs(
    cards.slice(1).map((card, index) => ({ key: card.key, dy: 80 })), values);

  return html`<div class="tune-layout">
    <div class="tune-stage">
      <div class="app-shell">
        <div class="sidebar"></div>
        <div class="practice-page">
          ${/* The app applies its layout class from the log tuning registry; the
           rig names it directly so the card head wears the same grid --
           without it the identity centres itself and the stage stops
           looking like the surface it exists to judge. */""}
          <section class="practice-card log-list-card log-layout-oneline">
            <div class="card-heading attempts-heading">
              <div><span class="eyebrow">Practice log</span><h3>Recent activity</h3></div>
            </div>
            <div class="log-list" ref=${listRef}>
              ${cards.map((card) => html`<${Card} key=${card.key} card=${card}
                open=${openKey === card.key}
                onToggle=${() => setOpenKey(openKey === card.key ? null : card.key)} />`)}
            </div>
          </section>
        </div>
      </div>
    </div>

    <div class="tune-panel">
      <h1>Feed and disclosure tuning</h1>
      <p class="tune-note">The cards above move through the app's own
        code${" "}—${" "}the same FLIP hook the practice log uses and the same
        disclosure every dropdown uses. Push a card in to judge the arrival and
        the shove; open one to judge the disclosure. Save writes these values
        into${" "}<code>ui/feedtuning.js</code> as the new shipped defaults.</p>
      ${prefersReducedMotion() ? html`<p class="tune-warn">Your OS is set to
        reduce motion, so everything here SNAPS and nothing will animate until
        that is off.</p>` : null}

      <div class="tune-showing">Whole gesture settles in ${Math.round(settle)}ms</div>
      <div class="tune-actions">
        <button class="primary" onclick=${pushOne}>↓ Push a new card in</button>
        <button onclick=${() => setOpenKey(openKey ? null : cards[0].key)}>
          ⇕ Open / close the top card</button>
        <button onclick=${() => { setCards(SEED); setOpenKey(null); }}>Reset the list</button>
      </div>

      <${ControlGroups} groups=${FEED_GROUPS} rows=${FEED_TUNABLES}
        values=${values} defaults=${FEED_DEFAULTS} onChange=${set} />

      <h2>Changed from shipped (${changedKeys.length})</h2>
      <pre class=${`tune-diff${changedKeys.length ? "" : " empty"}`}>${
        changedKeys.length
          ? changedKeys.sort().map((key) =>
              `${key}: ${FEED_DEFAULTS[key]} -> ${changed[key]}`).join("\n")
          : "nothing — this is exactly what ships"}</pre>
      <div class="tune-actions" style="margin-top:10px">
        <button class="save" disabled=${!changedKeys.length} onclick=${saveToRepo}>
          Save into the repo</button>
        <button onclick=${() => setValues(withFeedDefaults(null))}>
          Reset all to shipped</button>
      </div>
      ${status && html`<p class=${`tune-status ${status.kind}`}>${status.text}</p>`}
    </div>
  </div>`;
}

render(html`<${Inspector} />`, document.getElementById("root"));

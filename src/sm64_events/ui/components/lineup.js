// src/sm64_events/ui/components/lineup.js — the Lineup on the active practice card.
//
// The row of people you race (ui/lineup.js draws and moves it; this file
// feeds it and answers its gestures). Three things it adds to the core:
//   * the MODEL, from the ghost session (ui/lineupmodel.js), applied one at a
//     time so a burst of broadcasts never starts two animations at once;
//   * LOADING that never flickers: once the empty places show, they stay at
//     least MIN_LOADING_MS before the people arrive (round 4: "while the ghosts
//     are LOADING, and we're waiting, we're showing the classic iOS style");
//   * the swap popover, anchored under the seat it replaces.
import { h, render } from "preact";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { fmtIgtShort } from "../format.js";
import { createLineup } from "../lineup.js";
import { lineupModel } from "../lineupmodel.js";
import { RankIcon } from "./rankicon.js";
import { RivalSwap } from "./rivalpicker.js";

const html = htm.bind(h);
export const MIN_LOADING_MS = 400;
const sleep = ms => new Promise(resolve => { setTimeout(resolve, ms); });

// Caps are the real RankIcon, rendered into a slot the core places. Each slot
// is unmounted once the core has let go of it, so its style subscription ends.
function useCaps() {
  const slots = useRef(new Set());
  const capNode = useMemo(() => (person, size) => {
    const slot = document.createElement("span");
    slot.className = "rank-icon-slot";
    slot.style.setProperty("--icon-size", `${size}px`);
    render(h(RankIcon, { tier: person.tier, division: person.division, size }), slot);
    slots.current.add(slot);
    return slot;
  }, []);
  const sweep = () => {
    for (const slot of slots.current) {
      if (slot.isConnected) continue;
      render(null, slot);
      slots.current.delete(slot);
    }
  };
  useEffect(() => () => { for (const slot of slots.current) render(null, slot); slots.current.clear(); }, []);
  return { capNode, sweep };
}

/** Applies models in order, coalescing to the newest, and holds loading on screen long enough to read. */
function useDriver(core, model, sweep) {
  const latest = useRef(null);
  const applied = useRef(null);
  const loadingAt = useRef(null);
  const running = useRef(false);
  latest.current = model;
  useLayoutEffect(() => {
    if (!model || running.current || !core.current) return;
    running.current = true;
    (async () => {
      try {
        while (core.current && latest.current && latest.current !== applied.current) {
          const next = latest.current;
          const at = loadingAt.current;
          if (at != null && !next.loading && next.lineup === applied.current?.lineup) {
            const wait = MIN_LOADING_MS - (performance.now() - at);
            if (wait > 0) { await sleep(wait); continue; }
          }
          const first = applied.current == null;
          applied.current = next;
          loadingAt.current = next.loading ? (loadingAt.current ?? performance.now()) : null;
          await core.current.set(next, { animate: !first });
          sweep();
        }
      } finally {
        running.current = false;
      }
    })();
  }, [model]);
}

export function Lineup({ session, known, reachable, on, entityKey, strategies, write, retry,
                         onPickStrategy }) {
  const host = useRef(null);
  const core = useRef(null);
  const [swap, setSwap] = useState(null);
  const { capNode, sweep } = useCaps();
  const scope = session?.rivals?.scope || "strategy";
  const handlers = useRef({});
  handlers.current = {
    onSwap: (person, seat) => {
      const box = host.current.getBoundingClientRect();
      const at = seat.getBoundingClientRect();
      setSwap({ slot: person.slot, name: person.name, pinned: person.pinned,
        left: Math.max(0, Math.min(at.left - box.left, box.width - 300)), top: at.bottom - box.top + 4 });
    },
    onUnpin: person => write({ rivals: { scope, action: "reset", slot: person.slot }, target: session.target }),
    onChoose: choice => onPickStrategy(choice.strategy),
    onNote: note => { if (note.kind === "retry") retry(); },
  };
  useLayoutEffect(() => {
    core.current = createLineup(host.current, {
      capNode, fmt: fmtIgtShort,
      onSwap: (...args) => handlers.current.onSwap(...args),
      onUnpin: (...args) => handlers.current.onUnpin(...args),
      onChoose: (...args) => handlers.current.onChoose(...args),
      onNote: (...args) => handlers.current.onNote(...args),
    });
    return () => { core.current = null; };
  }, []);
  const raw = lineupModel({ session, known, reachable, on, entityKey, strategies });
  const key = raw ? JSON.stringify(raw) : null;
  const model = useMemo(() => raw, [key]);
  useDriver(core, model, sweep);
  // A popover belongs to the lineup it opened on; a new star or strategy closes it.
  const lineupId = model?.lineup;
  useEffect(() => { setSwap(null); }, [lineupId]);
  const rows = session?.rivals?.rows || [];
  return html`<div class="lineup-host ${model ? "" : "is-waiting"}">
    <div ref=${host}></div>
    ${session?.rivals?.simulation && html`<p class="lineup-simulation" role="status">${session.rivals.simulation}</p>`}
    ${swap && html`<${RivalSwap} target=${session.target} scope=${scope} rows=${rows} slot=${swap.slot}
      name=${swap.name} pinned=${swap.pinned} style=${`left:${swap.left}px;top:${swap.top}px`}
      onClose=${() => setSwap(null)}
      onUnpin=${() => { setSwap(null); return write({ rivals: { scope, action: "reset", slot: swap.slot }, target: session.target }); }}
      onAdd=${(id) => write({ rivals: { scope, action: "add", id, slot: swap.slot }, target: session.target })} />`}
  </div>`;
}

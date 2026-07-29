// src/sm64_events/ui/components/collapsible.js
//
// Card collapse: one primitive, every card.
//
// The user asked for this on all layouts, not just narrow ones ("we should
// just do this by default on all layouts"), because at 900x1180 the practice
// log is below two tall cards and unreachable without scrolling.
//
// Why this is a plain `height` animation and not the usual grid-0fr trick:
// every card it applies to ALREADY has a hard pixel height, because the user
// streams them in OBS and a card that reflowed mid-run would shift their
// capture. Both ends of the animation are therefore real numbers and `height`
// interpolates between them directly. The OBS contract survives collapse for
// the same reason -- a collapsed card is still a fixed height, just a smaller
// one, and it only changes when the user asks it to.
//
// State is an open-SET inverted: we store the COLLAPSED ids, so "nothing
// stored" means every card is open, which is the right first-run state. Same
// shape as grouplist.js's open-set, opposite polarity, for the same reason --
// the default must need no entry.
import { useCallback, useState } from "preact/hooks";
import { h } from "preact";
import htm from "htm";
import { Icon } from "./icons.js";

const html = htm.bind(h);

const STORE_KEY = "sm64.cardsCollapsed";

function readCollapsed() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch {
    return new Set();          // private mode, quota, corrupt JSON — open is safe
  }
}

/**
 * `[collapsed, toggle]` for one card id, persisted across reloads.
 *
 * Reads storage per hook rather than through a shared store: there are five
 * of these on a page, they change only on a click, and a context provider
 * would be more moving parts than the thing it coordinates.
 */
export function useCollapsed(cardId) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed().has(cardId));
  const toggle = useCallback(() => {
    const next = readCollapsed();
    if (next.has(cardId)) next.delete(cardId);
    else next.add(cardId);
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify([...next]));
    } catch {
      /* not persisting is survivable; refusing to collapse is not */
    }
    setCollapsed(next.has(cardId));
  }, [cardId]);
  return [collapsed, toggle];
}

/**
 * The affordance. Lives in the card's own heading, and is the whole heading's
 * business: `aria-expanded` carries the state, so the chevron is decoration
 * and screen readers do not need it described.
 */
export function CollapseToggle({ collapsed, toggle, label }) {
  return html`<button type="button" class="card-collapse"
      aria-expanded=${collapsed ? "false" : "true"}
      title=${`${collapsed ? "Expand" : "Collapse"} ${label}`}
      onclick=${toggle}>
    <${Icon} name="chevron" size=${18} />
  </button>`;
}

/** The class a collapsible card wears; keeps the string in one place. */
export function cardClass(collapsed) {
  return collapsed ? "is-collapsed" : "";
}

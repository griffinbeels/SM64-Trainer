// src/sm64_events/ui/components/strategystep.js — the picker's THIRD layer:
// which strategy to practice a just-picked star or segment WITH. A separate
// file from entitymodal.js on purpose — that component "knows nothing about
// what it is picking" (its own header comment), and a strategy card is
// domain data the shared grid must never learn. The POST /api/target write
// belongs here, where the entity AND the strategy are both known.
//
// Nothing is written until a card is clicked: entering this step, backing
// out (onBack) or closing the dialog (onClose, Esc, backdrop) beforehand
// must leave the user's existing target and strategy exactly as they were —
// the whole point of a three-layer picker is that changing your mind at any
// layer costs nothing (spec 2026-07-25-target-picker-strategy-step).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { parseSegmentId, parseStarId } from "../entities.js";
import { Hat } from "./hat.js";
import { capName, divisionDigit } from "./caps.js";
import { StratModal } from "./stratmodal.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

// Entering this step unmounts the cell that was just clicked, so focus falls
// to <body> — and Modal only traps Tab at its first/last focusable, so from
// <body> a Tab walks out of the dialog into the page behind it. Preact
// REUSES the Modal instance across the grid->step transition (same
// component type, same position in the tree), so Modal's own mount-only
// focus effect never re-runs to rescue this. Back is both a real target and
// what a keyboard user most likely wants next — same fix as the grid's own
// drill-in (entitymodal.js's focusOnDrillIn).
const focusOnEntry = (node) => { if (node) node.focus(); };

export function StrategyStep({ value, option, onBack, onClose }) {
  // `option` (the picked cell's full object) is part of the nextStep
  // contract every caller receives, but this step has nothing to add to
  // what it already shows — the enclosing Modal's title is `option.name`
  // (entitymodal.js's pendingOption branch) before this component ever
  // mounts.
  const segmentId = parseSegmentId(value);
  const parsedStar = segmentId == null ? parseStarId(value) : null;
  const entity = segmentId != null
    ? `segment:${segmentId}` : `star:${parsedStar.course}:${parsedStar.star}`;

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [showNew, setShowNew] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null); setError(null);
    getJSON(`/api/target/strategies?entity=${encodeURIComponent(entity)}`)
      .then((response) => { if (alive) setData(response); })
      .catch((fetchError) => { if (alive) setError(String(fetchError)); });
    return () => { alive = false; };
  }, [value]);

  // Exactly ONE write per commit, then the caller refreshes. `strat_tag`
  // must always be SENT, even as `null` — api.py:594 reads whether the key
  // is present at all (model_fields_set) to tell "clear the strategy" from
  // "leave it alone", and omitting the key here would silently mean the
  // latter for every card, including "No strategy".
  async function commit(stratTag) {
    if (saving) return;
    setSaving(true);
    const body = segmentId != null
      ? { kind: "segment", segment_id: Number(segmentId), strat_tag: stratTag }
      : { course_id: Number(parsedStar.course), star_id: Number(parsedStar.star),
          strat_tag: stratTag };
    try {
      await send("POST", "/api/target", body);
      onClose();
    } catch (writeError) {
      // A dropped write must not look like it worked — stay open, matching
      // stratpicker.js's own recovery for the same failure.
      window.alert(String(writeError));
      setSaving(false);
    }
  }

  return html`<${h.Fragment}>
    <button type="button" class="entity-back" ref=${focusOnEntry} onclick=${onBack}>
      <${Icon} name="chevron" size=${15} /> Back
    </button>
    ${error ? html`<div class="modal-error">${error}</div>`
      : !data ? html`<div class="stable-empty compact">Loading strategies…</div>`
      : html`<${StrategyCards} data=${data} saving=${saving} commit=${commit}
          onOpenNew=${() => setShowNew(true)} />`}
    ${showNew ? html`<${StratModal} entity=${entity}
        existing=${(data ? data.strategies : []).map((strat) => strat.name)}
        onSaved=${(stratName) => { setShowNew(false); commit(stratName); }}
        onClose=${() => setShowNew(false)} />` : null}
  <//>`;
}

// Split out purely so the zero-strategy note below can sit alongside the
// SAME two evergreen cards ("No strategy", "+ New strategy…") without a
// second copy of their markup.
// The strategy card's <Hat> below is called UNCONDITIONALLY with
// tier=${strat.rank}, which can be null for an unranked strategy -- unlike
// the two AGGREGATE surfaces (MareloBar, the Rank tab card) that gate Hat
// behind `tier ? <Hat/> : "–"` (ui.md's Cap icon row, final review I5). This
// call site differs on purpose: the grey no-tier cap sits directly above the
// "Unranked" label two lines down, which disambiguates it, and the plan
// sanctioned rendering Hat's own no-rank state here rather than gating it
// (M3, final review 2026-07-26) -- do not "fix" this back to the aggregate
// pattern without re-reading that finding.
function StrategyCards({ data, saving, commit, onOpenNew }) {
  const { current, allow_blank, strategies } = data;
  return html`<${h.Fragment}>
    <div class="strat-grid">
      ${allow_blank ? html`<button type="button" disabled=${saving}
          class="strat-card ${current == null ? "needs-strat" : ""}"
          onclick=${() => commit(null)}>
        <span class="entity-clear-mark">×</span>
        <span class="entity-clear-label">No strategy</span>
      </button>` : null}
      ${strategies.map((strat) => html`<button type="button" key=${strat.name}
          disabled=${saving} class="strat-card" onclick=${() => commit(strat.name)}>
        <${Hat} tier=${strat.rank} division=${strat.division} size=${32} />
        <span class="strat-rank-name">${strat.rank
          ? `${capName(strat.rank)} ${divisionDigit(strat.division)}` : "Unranked"}</span>
        <span class="strat-name">${strat.name}</span>
        ${strat.name === current ? html`<span class="strat-current">● current</span>` : null}
        <span class="strat-pb meta">${strat.pb_display
          ? `PB ${strat.pb_display}` : "no attempts"}</span>
      </button>`)}
      <button type="button" class="strat-card" disabled=${saving} onclick=${onOpenNew}>
        <span class="entity-clear-mark">+</span>
        <span class="entity-clear-label">New strategy…</span>
      </button>
    </div>
    ${strategies.length === 0
      ? html`<div class="stable-empty compact">No strategies recorded here yet.</div>`
      : null}
  <//>`;
}

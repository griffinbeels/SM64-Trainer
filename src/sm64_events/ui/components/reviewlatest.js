import { h } from "preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import htm from "htm";
import { AttemptDrawer } from "./attemptdrawer.js";
import { Icon } from "./icons.js";
import { Modal } from "./modal.js";
import { entityKey } from "../entitysection.js";
import { newestReview, reviewFromEvent, reviewRows } from "../latestreview.js";

const html = htm.bind(h);

export function ReviewLatest({ t, openCompare }) {
  const [latest, setLatest] = useState(null);
  const [selected, setSelected] = useState(null);
  const seen = useRef(new WeakSet());
  const session = useRef(null);
  const target = useRef(null);
  const completionGroup = useRef({ at: null, preferred: null });
  const rows = useMemo(() => reviewRows(t.view), [t.view]);
  const latestRef = useRef(null);
  latestRef.current = latest;
  useEffect(() => {
    const sessionId = t.view?.session?.id;
    if (session.current !== sessionId) {
      session.current = sessionId; setLatest(newestReview(rows));
      completionGroup.current = { at: null, preferred: null };
    } else {
      // A reconnect may miss completion messages. The refreshed view can
      // advance the button while the separately selected review stays put.
      setLatest(previous => newestReview([previous, newestReview(rows)].filter(Boolean),
        completionGroup.current.preferred));
    }
    const completed = (t.feed || []).filter(event => event.type === "attempt_completed"
      && event.payload.session_id === sessionId && !seen.current.has(event));
    completed.forEach(event => seen.current.add(event));
    if (completed.length) {
      const incoming = completed.map(reviewFromEvent);
      const newest = newestReview(incoming);
      if (completionGroup.current.at !== newest.ended_utc)
        completionGroup.current = { at: newest.ended_utc, preferred: target.current };
      const preferred = completionGroup.current.preferred;
      setLatest(previous => newestReview([previous, ...incoming].filter(Boolean), preferred));
    }
    target.current = t.view?.target ? entityKey(t.view.target) : null;
  }, [t.feed, t.view, rows]);
  useEffect(() => {
    const key = event => {
      if (event.code !== "KeyR" || event.ctrlKey || event.altKey || event.metaKey
          || event.shiftKey || event.repeat || event.defaultPrevented
          || event.target?.isContentEditable
          || event.target?.closest?.('input,textarea,select,[role="dialog"]')) return;
      if (!latestRef.current) return;
      event.preventDefault(); setSelected(latestRef.current);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const liveRow = selected && rows.find(row => row.id === selected.id);
  const shown = liveRow || selected;
  return html`<div class="review-latest-entry">
    <button class="review-latest-button" disabled=${!latest}
      title="Review the latest completed attempt (R while the Trainer has focus)"
      onclick=${() => setSelected(latest)}><${Icon} name="play" size=${17} />
      Review latest <kbd>R</kbd></button>
    ${latest && html`<span class="meta review-latest-label">${latest.label}</span>`}
    ${shown && html`<${Modal} title=${shown.label} icon="play" size="large"
      description=${`Attempt ${shown.id} · ${shown.outcome || "completed"}`}
      onClose=${() => setSelected(null)}>
      <div class="latest-review-surface" tabindex="-1" autofocus>
        <${AttemptDrawer} key=${shown.id} attemptId=${shown.id} targetLabel=${shown.label}
          onTemplateMarked=${t.refresh}
          onCompare=${shown.entity ? () => {
            setSelected(null); openCompare({ attemptId: shown.id, entity: shown.entity,
              strat: shown.strat_tag });
          } : undefined} />
      </div>
    <//>`}
  </div>`;
}

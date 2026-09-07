import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
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
  const latestRef = useRef(null);
  latestRef.current = latest;
  useEffect(() => {
    const sessionId = t.view?.session?.id;
    if (session.current !== sessionId) {
      session.current = sessionId; setLatest(newestReview(reviewRows(t.view)));
    } else if (!latestRef.current) setLatest(newestReview(reviewRows(t.view)));
    const completed = (t.feed || []).filter(event => event.type === "attempt_completed"
      && event.payload.session_id === sessionId && !seen.current.has(event));
    completed.forEach(event => seen.current.add(event));
    if (completed.length) setLatest(newestReview(completed.map(reviewFromEvent), target.current));
    target.current = t.view?.target ? entityKey(t.view.target) : null;
  }, [t.feed, t.view]);
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
  const liveRow = selected && reviewRows(t.view).find(row => row.id === selected.id);
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
      <div class="latest-review-surface">
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

// src/sm64_events/ui/components/dailystarmodal.js — the Daily Star
// announcement (task 0111): "a little notification modal in the center of the
// screen" the first time the app is open on a new local date, on the shared
// modal shell the Setup screen uses. It informs and nothing else — his words:
// "The modal itself doesn't really need to do anything other than inform the
// user about the daily star."
//
// Root-mounted (app.js) like the update popup, so the browser tab and the
// desktop window behave alike, and the server's seen-date means whichever of
// them announces a day, the other does not repeat it.
//
// It waits its turn: the Setup screen can open on the same page load, and two
// stacked dialogs read as a fault. The announcement shows only once no other
// modal has been on screen for a full check interval.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { Modal } from "./modal.js";
import { entityIconSrc, fallbackToGenericStar } from "./entityicons.js";
import { fmtIgtShort } from "../format.js";

const html = htm.bind(h);
const QUIET_CHECK_MS = 900;

function useQuietScreen(wanted) {
  const [quiet, setQuiet] = useState(false);
  useEffect(() => {
    if (!wanted) { setQuiet(false); return undefined; }
    let clearChecks = 0;
    const timer = setInterval(() => {
      clearChecks = document.querySelector(".modal-backdrop") ? 0 : clearChecks + 1;
      if (clearChecks >= 2) { setQuiet(true); clearInterval(timer); }
    }, QUIET_CHECK_MS);
    return () => clearInterval(timer);
  }, [wanted]);
  return quiet;
}

export function DailyStarCard({ t, daily }) {
  const today = daily.today;
  const todayText = today && today.frames != null
    ? fmtIgtShort(today.frames)
    : "—";
  const todayNote = !today
    ? "not played yet"
    : today.frames == null
      ? `${today.attempts} ${today.attempts === 1 ? "try" : "tries"}, no finish yet`
      : `${today.attempts} ${today.attempts === 1 ? "try" : "tries"}`;
  return html`<div class="daily-card">
    <div class="daily-art daily-star-foil">
      <img src=${entityIconSrc(t, daily.entity_key || daily.key)} alt=""
        onerror=${(errorEvent) => fallbackToGenericStar(errorEvent, 0)} draggable="false" />
    </div>
    <div class="daily-where">Day ${daily.day} · ${daily.course_name}</div>
    <div class="daily-name">${daily.label}</div>
    <p class="daily-blurb">Everyone practising today gets this same star.
      Your best time today is your Daily Star PB.</p>
    <dl class="daily-times">
      <div><dt>Today's best</dt><dd>${todayText}</dd><small>${todayNote}</small></div>
      <div><dt>Your PB</dt><dd>${daily.pb_frames != null ? fmtIgtShort(daily.pb_frames) : "—"}</dd>
        <small>${daily.pb_frames != null ? "saved" : "none saved"}</small></div>
    </dl>
  </div>`;
}

export function DailyStarModal({ t }) {
  const daily = t.dailyStar;
  const due = !!(daily && !daily.seen);
  const quiet = useQuietScreen(due);
  if (!due || !quiet) return null;
  const close = () => t.markDailyStarSeen();
  return html`<${Modal} title="Daily Star" icon="target" size="daily"
      description="A new star to practise, the same for everyone today."
      onClose=${close}
      footer=${html`<button class="primary-button" autofocus onclick=${close}>Got it</button>`}>
    <${DailyStarCard} t=${t} daily=${daily} />
  </${Modal}>`;
}

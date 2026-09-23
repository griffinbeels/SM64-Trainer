// src/sm64_events/ui/components/dailystarmodal.js — the Daily Star
// announcement (task 0111): "a little notification modal in the center of the
// screen" the first time the app is open on a new local date, on the shared
// modal shell the Setup screen uses. It informs and nothing else — his words:
// "The modal itself doesn't really need to do anything other than inform the
// user about the daily star." Round 1: few words ("caveman speak"), your PB
// beside the next goal, and a live TIME LEFT that turns the page to the next
// day's star when it runs out.
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
import { RankIcon } from "./rankicon.js";
import { capName, divisionDigit } from "./caps.js";
import { entityIconSrc, fallbackToGenericStar } from "./entityicons.js";
import { fmtIgtShort, fmtSeconds } from "../format.js";
import { msUntilEndOfDate } from "../dailystar.js";

const html = htm.bind(h);
const QUIET_CHECK_MS = 900;
// How long "TIMES UP!" holds once the next day's star is in hand, before the
// page turns. Page-turn length is the CSS animation's; keep the two equal.
const TIMES_UP_HOLD_MS = 1200;
const TURN_MS = 560;

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

// Re-renders on each whole second, so the countdown ticks with the clock.
function useSecondTick() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    let timer = null;
    const tick = () => {
      setNow(Date.now());
      timer = setTimeout(tick, 1005 - (Date.now() % 1000));
    };
    timer = setTimeout(tick, 1005 - (Date.now() % 1000));
    return () => clearTimeout(timer);
  }, []);
  return now;
}

export function fmtTimeLeft(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const pad = (n) => String(n).padStart(2, "0");
  return `${Math.floor(total / 3600)}:${pad(Math.floor(total / 60) % 60)}:${pad(total % 60)}`;
}

function NextGoal({ goal, hasPb }) {
  if (!goal) {
    return html`<div><dt>Next Goal</dt><dd>${hasPb ? "Top rank" : "—"}</dd>
      <small>${hasPb ? "nothing above you" : "no ranks yet"}</small></div>`;
  }
  return html`<div><dt>Next Goal</dt><dd>${fmtSeconds(goal.cs / 100)}</dd>
    <small class="daily-goal-rank"><${RankIcon} tier=${goal.tier} division=${goal.division} size=${14} />
      ${capName(goal.tier)} ${divisionDigit(goal.division)}</small></div>`;
}

export function DailyStarCard({ t, daily }) {
  const hasPb = daily.pb_frames != null;
  return html`<div class="daily-card">
    <div class="daily-art daily-star-foil">
      <img src=${entityIconSrc(t, daily.entity_key || daily.key)} alt=""
        onerror=${(errorEvent) => fallbackToGenericStar(errorEvent, 0)} draggable="false" />
    </div>
    <div class="daily-where">Day ${daily.day} · ${daily.course_name}</div>
    <div class="daily-name">${daily.label}</div>
    <p class="daily-blurb">Everyone plays this star today.</p>
    <dl class="daily-times">
      <div><dt>Your PB</dt><dd>${hasPb ? fmtIgtShort(daily.pb_frames) : "—"}</dd>
        <small>${hasPb ? "saved" : "none yet"}</small></div>
      <${NextGoal} goal=${daily.next_goal} hasPb=${hasPb} />
    </dl>
  </div>`;
}

// The open dialog. It owns the page on screen, so the store clearing its
// payload at midnight never closes it: "TIMES UP!" holds, then the next day's
// star arrives as the next page.
function DailyStarDialog({ t, onClose }) {
  const [pages, setPages] = useState(() => ({ current: t.dailyStar, leaving: null }));
  const now = useSecondTick();
  const incoming = t.dailyStar;
  useEffect(() => {
    if (!incoming) return undefined;
    if (incoming.date === pages.current.date) {
      if (incoming !== pages.current) setPages((p) => ({ ...p, current: incoming }));
      return undefined;
    }
    const hold = setTimeout(() => {
      setPages((p) => ({ current: incoming, leaving: p.current }));
    }, TIMES_UP_HOLD_MS);
    return () => clearTimeout(hold);
  }, [incoming]);
  useEffect(() => {
    if (!pages.leaving) return undefined;
    const done = setTimeout(() => setPages((p) => ({ ...p, leaving: null })), TURN_MS);
    return () => clearTimeout(done);
  }, [pages.leaving]);
  const left = msUntilEndOfDate(pages.current.date, now);
  const timesUp = left <= 0;
  const close = () => onClose(pages.current.date);
  return html`<${Modal} title="Daily Star" icon="target" size="daily" onClose=${close}
      footer=${html`<div class="daily-timer ${timesUp ? "times-up" : ""}" role="timer">
          ${timesUp ? html`<b>TIMES UP!</b>`
            : html`<span>TIME LEFT</span><b>${fmtTimeLeft(left)}</b>`}</div>
        <button class="primary-button" autofocus onclick=${close}>Got it</button>`}>
    <div class="daily-pages">
      ${pages.leaving ? html`<div class="daily-page turn-out" key=${pages.leaving.date}>
        <${DailyStarCard} t=${t} daily=${pages.leaving} /></div>` : null}
      <div class="daily-page ${pages.leaving ? "turn-in" : ""}" key=${pages.current.date}>
        <${DailyStarCard} t=${t} daily=${pages.current} /></div>
    </div>
  </${Modal}>`;
}

export function DailyStarModal({ t }) {
  const due = !!(t.dailyStar && !t.dailyStar.seen);
  const quiet = useQuietScreen(due);
  const [open, setOpen] = useState(false);
  useEffect(() => { if (due && quiet) setOpen(true); }, [due, quiet]);
  if (!open) return null;
  return html`<${DailyStarDialog} t=${t}
    onClose=${(date) => { setOpen(false); t.markDailyStarSeen(date); }} />`;
}

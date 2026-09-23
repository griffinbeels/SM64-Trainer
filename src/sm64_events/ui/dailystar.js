// src/sm64_events/ui/dailystar.js — the Daily Star's client half (task 0111).
//
// The server decides WHICH entry a date deals (ranks/dailystar.py) and what
// today's best is (server/dailystar_api.py). This module owns the one thing
// only the browser knows: the local calendar date, and the moment it changes.
// "if they had it open when we shifted from 11:59pm to 12:00am" — a timer
// armed for the next local midnight, re-checked on focus because a sleeping
// laptop does not run timers on time.
import { useEffect, useState } from "preact/hooks";
import { getJSON, send } from "./api.js";
import { useIdentityFetch } from "./refetch.js";

const pad = (n) => String(n).padStart(2, "0");

export function localDateKey(now = new Date()) {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function msUntilNextLocalMidnight(now = new Date()) {
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return next - now;
}

function useLocalDate() {
  const [date, setDate] = useState(localDateKey);
  useEffect(() => {
    let timer = null;
    const check = () => setDate(localDateKey());
    const arm = () => {
      clearTimeout(timer);
      timer = setTimeout(() => { check(); arm(); },
                         msUntilNextLocalMidnight() + 500);
    };
    const wake = () => { check(); arm(); };
    arm();
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, []);
  return date;
}

// `rev` is the store's attempt-completion counter, so today's best follows a
// grab without a page reload.
export function useDailyStar(rev) {
  const date = useLocalDate();
  const [daily, setDaily] = useState(null);
  // A new DATE is a new star: clear so yesterday's never shows under today.
  // An attempt (`rev`) only refreshes today's best, in place.
  useIdentityFetch(date, rev, (cleared) => {
    let live = true;
    if (cleared) setDaily(null);
    getJSON(`/api/daily-star?date=${date}`)
      .then((payload) => { if (live) setDaily(payload); })
      .catch(() => {});
    return () => { live = false; };
  });
  const markSeen = () => {
    if (!daily) return;
    setDaily({ ...daily, seen: true });
    send("POST", "/api/daily-star/seen", { date: daily.date }).catch(() => {});
  };
  return { daily, markSeen };
}

// The Daily Star for a practice cell, or null. Callers pass the cell's own
// entity key; the server already resolved a fight's seed key to this
// install's `segment:<id>`.
export function dailyFor(t, entityKey) {
  const daily = t && t.dailyStar;
  return daily && daily.entity_key === entityKey ? daily : null;
}

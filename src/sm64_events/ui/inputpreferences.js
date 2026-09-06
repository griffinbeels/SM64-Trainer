// Browser display preferences, shared by every mounted timeline and tab.
// Template documents and their local target bindings remain server-owned.
import { useEffect, useState } from "preact/hooks";

const ROW_KEY = "sm64.inputOverlayRows";
const CHANGE_KEY = "sm64.inputTemplatesChanged";
const CHANGE_EVENT = "sm64-input-templates-changed";
const ROW_EVENT = "sm64-input-overlay-rows";
let memoryRows = {};

function readRows() {
  try {
    const value = JSON.parse(localStorage.getItem(ROW_KEY));
    memoryRows = value && typeof value === "object" && !Array.isArray(value) ? value : {};
    return memoryRows;
  } catch { return memoryRows; }
}

export function useOverlayRows() {
  const [rows, setRows] = useState(readRows);
  useEffect(() => {
    const changed = (event) => {
      if (event.type !== "storage" || event.key === ROW_KEY || event.key === null)
        setRows(event.type === ROW_EVENT ? event.detail : readRows());
    };
    window.addEventListener(ROW_EVENT, changed);
    window.addEventListener("storage", changed);
    return () => {
      window.removeEventListener(ROW_EVENT, changed);
      window.removeEventListener("storage", changed);
    };
  }, []);
  const toggle = (key, visible) => {
    const next = { ...readRows(), [key]: visible };
    memoryRows = next;
    try { localStorage.setItem(ROW_KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
    setRows(next);
    window.dispatchEvent(new CustomEvent(ROW_EVENT, { detail: next }));
  };
  return [(key) => rows[key] !== false, toggle];
}

export function templatesChanged() {
  try { localStorage.setItem(CHANGE_KEY, `${Date.now()}-${Math.random()}`); } catch { /* private mode */ }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function useTemplateRevision() {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const changed = (event) => {
      if (event.type !== "storage" || event.key === CHANGE_KEY)
        setRevision((value) => value + 1);
    };
    window.addEventListener(CHANGE_EVENT, changed);
    window.addEventListener("storage", changed);
    return () => {
      window.removeEventListener(CHANGE_EVENT, changed);
      window.removeEventListener("storage", changed);
    };
  }, []);
  return revision;
}

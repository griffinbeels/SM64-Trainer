// Segment definition saves share one server-authoritative scope contract.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "./api.js";
import { Icon } from "./components/icons.js";

const html = htm.bind(h);

export function useSegmentAuthoring() {
  const [capability, setCapability] = useState(null);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setCapability(null); setError(null);
    getJSON("/api/segments/authoring").then((body) => {
      if (typeof body.can_share !== "boolean"
          || body.default_scope !== (body.can_share ? "shared" : "personal")) {
        throw new Error("The server returned invalid segment save options.");
      }
      if (!cancelled) setCapability(body);
    }).catch((err) => { if (!cancelled) setError(String(err)); });
    return () => { cancelled = true; };
  }, [attempt]);
  return { ready: capability != null, canShare: capability?.can_share || false,
    defaultScope: capability?.default_scope || null, error,
    retry: () => setAttempt((n) => n + 1) };
}

export function segmentSaveUrl(id, scope) {
  if (scope !== "shared" && scope !== "personal") {
    throw new Error("Wait for segment save options before saving.");
  }
  return `/api/segments${id == null ? "" : `/${id}`}?scope=${scope}`;
}

export function segmentSaveMessage(response, scope, canShare) {
  if (scope === "shared") {
    if (response?.scope !== "shared") {
      throw new Error("The server did not confirm saving this segment for everyone.");
    }
    return response.message || "Saved for the next release";
  }
  return canShare ? "Saved only for me" : "Saved";
}

// Keep the minted id even if a later link/refresh callback fails. One active
// save per mounted editor also prevents a double click creating two rows.
export function useSegmentSave({ authoring, initialId = null, definition,
    onSaved, onCreated, t, disabled = false }) {
  const savedIdRef = useRef(initialId);
  const activeRef = useRef(false);
  const timerRef = useRef(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [savedScope, setSavedScope] = useState(null);
  const [message, setMessage] = useState(null);
  const [savedFlash, setSavedFlash] = useState(false);
  useEffect(() => () => clearTimeout(timerRef.current), []);
  const rememberCreated = (id) => { savedIdRef.current = id; };
  const finishSave = () => { activeRef.current = false; setSaving(false); };
  async function save(scope = authoring.defaultScope) {
    if (activeRef.current || disabled) return false;
    activeRef.current = true;
    setSaving(true); setError(null); setMessage(null); setSavedFlash(false);
    try {
      const id = savedIdRef.current;
      const response = await send(id == null ? "POST" : "PUT",
        segmentSaveUrl(id, scope), definition());
      const savedId = id ?? response.id;
      rememberCreated(savedId);
      const receipt = segmentSaveMessage(response, scope, authoring.canShare);
      if (id == null && onCreated) await onCreated(savedId);
      await onSaved(savedId);
      setSavedScope(scope); setMessage(receipt); setSavedFlash(true);
      if (t?.setNotice) t.setNotice(receipt);
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setSavedFlash(false), 2000);
      return true;
    } catch (err) { setError(String(err)); return false; }
    finally { finishSave(); }
  }
  return { save, saving, error, savedScope, message, savedFlash,
    clearFeedback: () => { setError(null); setMessage(null); setSavedFlash(false); } };
}

export function shippedSegmentWarning(segment, authoring, savedScope) {
  if (!authoring.ready) return "Checking how edits to this shipped movement will be saved…";
  if (authoring.canShare && savedScope !== "personal") {
    return "Save for everyone updates the default for the next release. Personal customizations stay protected. Save only for me keeps your edit personal and stops automatic updates to your copy.";
  }
  return segment.seed_dirty || savedScope === "personal"
    ? "This is your personal copy. It stops updating when a new version of the app ships a better version of this movement. Reset puts it back."
    : "Saving an edit creates your personal copy and stops automatic updates to this movement. Reset puts it back.";
}

const SAVE_CONTROL_DEFAULTS = { disabled: false, busy: false, busyLabel: "Saving…",
  waitingLabel: null, personalLabel: "Save segment", savedScope: null,
  savedFlash: false, disabledReason: "" };

export function SegmentSaveControls(props) {
  const { authoring, onSave, disabled, busy, busyLabel, waitingLabel,
    personalLabel, savedScope, savedFlash, disabledReason } = {
    ...SAVE_CONTROL_DEFAULTS, ...props };
  if (!authoring.ready) return html`<span class="meta" role=${authoring.error ? "alert" : "status"}>
    ${authoring.error || "Checking save options…"}
    ${authoring.error && html`${" "}<button type="button" onclick=${authoring.retry}>Retry save options</button>`}
  </span>`;
  const primaryScope = authoring.defaultScope;
  const savingDisabled = disabled || busy;
  return html`${authoring.canShare && html`<${SaveButton}
    disabled=${savingDisabled} title=${disabledReason}
    saved=${savedFlash && savedScope === "personal"}
    label=${saveButtonLabel(busy, busyLabel, waitingLabel,
      savedFlash && savedScope === "personal", "Saved only for me", "Save only for me")}
    onClick=${() => onSave("personal")} />`}
  <${SaveButton} primary=${true} disabled=${savingDisabled} title=${disabledReason}
    saved=${savedFlash && savedScope === primaryScope}
    label=${saveButtonLabel(busy, busyLabel, waitingLabel,
      savedFlash && savedScope === primaryScope,
      primaryScope === "shared" ? "Saved for the next release" : "Saved",
      primaryScope === "shared" ? "Save for everyone" : personalLabel)}
    onClick=${() => onSave(primaryScope)} />`;
}

function saveButtonLabel(busy, busyLabel, waitingLabel, saved, savedLabel, label) {
  if (busy) return busyLabel;
  if (waitingLabel) return waitingLabel;
  return saved ? savedLabel : label;
}

function SaveButton({ primary, disabled, title, saved, label, onClick }) {
  return html`<button type="button" class=${primary ? "primary-button" : ""}
      disabled=${disabled} title=${title} onclick=${onClick}>
    <${Icon} name=${saved ? "check" : "save"} size=${16} />${" "}${label}
  </button>`;
}

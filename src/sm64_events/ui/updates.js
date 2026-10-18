import { useEffect, useState, useCallback } from "preact/hooks";
import { getJSON, send } from "./api.js";

export function updateOfferVisible(status, applying, forced, dismissed) {
  if (!status?.update_available) return false;
  return applying || (!dismissed && (forced || status.skipped !== status.latest));
}

export function useUpdates() {
  // --- auto-update (shared so the header "Check for updates" button and the
  // popup agree on one status / one in-flight install) ---
  const [update, setUpdate] = useState(null);              // /api/update/status dict
  const [updateForced, setUpdateForced] = useState(false); // manual check found one -> show despite Skip/Later
  const [updateApplying, setUpdateApplying] = useState(false);
  const [updateMsg, setUpdateMsg] = useState("");          // transient header toast
  const [betaUpdatesBusy, setBetaUpdatesBusy] = useState(false);
  const [updateDismissed, setUpdateDismissed] = useState(false);
  const fetchUpdate = useCallback(async (force) => {
    try {
      const st = await getJSON("/api/update/status" + (force ? "?force=1" : ""));
      setUpdate(st);
      return st;
    } catch { return null; }
  }, []);
  useEffect(() => {
    // Check quietly in the background. Only an actionable offer interrupts;
    // a refresh must not wait for this request before practice or sign-in.
    void fetchUpdate(false);
  }, [fetchUpdate]);
  useEffect(() => { if (updateForced) setUpdateDismissed(false); }, [updateForced]);
  const dismissUpdate = useCallback(() => {
    setUpdateDismissed(true);
    setUpdateForced(false);
  }, []);
  const checkUpdates = useCallback(async () => {           // the header button
    setUpdateMsg("Checking…");
    const st = await fetchUpdate(true);                    // force=1 bypasses the server cache
    if (!st) setUpdateMsg("Check failed");
    else if (st.update_available) { setUpdateForced(true); setUpdateMsg(""); }
    else if (st.error) setUpdateMsg(`Check failed: ${st.error}`);
    else if (!st.frozen) setUpdateMsg(`v${st.current} — updates run in the packaged app`);
    else setUpdateMsg(`Up to date · v${st.current}`);
  }, [fetchUpdate]);
  const setBetaUpdates = useCallback(async (enabled) => {
    setBetaUpdatesBusy(true);
    setUpdateMsg("Checking…");
    try {
      const st = await send("PUT", "/api/update/preferences", { receive_beta_updates: enabled });
      setUpdate(st);
      setUpdateForced(!!st.update_available);
      setUpdateMsg(st.update_available ? "" : st.error
        ? `Update check failed: ${st.error}` : `Up to date · v${st.current}`);
    } catch (error) {
      setUpdateMsg(`Could not change beta updates: ${error.message}`);
    } finally { setBetaUpdatesBusy(false); }
  }, []);
  const repairUpdate = useCallback(async () => {
    setUpdateMsg("Checking…");
    try {
      const st = await getJSON("/api/update/status?force=1&repair=1");
      setUpdate(st);
      setUpdateForced(!!st.update_available);
      setUpdateMsg(st.update_available ? "" : "No compatible repair is available. Keep this version and try again later.");
    } catch (error) { setUpdateMsg(`Repair check failed: ${error.message}`); }
  }, []);
  useEffect(() => {   // auto-clear the toast, but keep "Checking…" until the result lands
    if (!updateMsg || updateMsg === "Checking…") return;
    const id = setTimeout(() => setUpdateMsg(""), 5000);
    return () => clearTimeout(id);
  }, [updateMsg]);
  const applyUpdate = useCallback(async () => {
    setUpdateApplying(true);
    try {
      const res = await send("POST", "/api/update/apply" + (update?.repair ? "?repair=1" : ""));
      if (!res || (res.state !== "downloading" && res.state !== "installing")) {
        // begin_apply refused WITHOUT starting the worker (re-check failed,
        // folder not writable): the service stays "idle", so the status poll
        // would render the button-less "Installing…" branch forever. Unstick
        // the popup and surface the reason as a header toast.
        setUpdateApplying(false);
        setUpdateMsg("Update could not start" +
                     (res && res.error ? `: ${res.error}` : ""));
      }
    } catch (e) {
      setUpdateApplying(false);
      setUpdateMsg(`Update could not start: ${e.message}`);
    }
  }, [update?.repair]);
  useEffect(() => {   // poll progress while installing; the WS drop on restart ends the session
    if (!updateApplying) return;
    const id = setInterval(() => fetchUpdate(false), 700);
    return () => clearInterval(id);
  }, [updateApplying, fetchUpdate]);
  const skipUpdate = useCallback(async (version) => {
    setUpdate((u) => (u ? { ...u, skipped: version } : u));
    setUpdateForced(false);
    setUpdateDismissed(true);
    try { await send("POST", "/api/update/skip", { version }); } catch { /* best-effort persistence */ }
  }, []);

  return { update, updateForced, setUpdateForced, updateApplying,
    setUpdateApplying, updateMsg, checkUpdates, applyUpdate, skipUpdate,
    betaUpdatesBusy, setBetaUpdates, repairUpdate, refreshUpdates: fetchUpdate,
    dismissUpdate,
    updateVisible: updateOfferVisible(update, updateApplying, updateForced, updateDismissed) };
}

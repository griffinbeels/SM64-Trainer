import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

export function ReportingSettings() {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    getJSON("/api/diagnostics/reporting")
      .then(value => { if (active) setStatus(value); })
      .catch(() => { if (active) setMessage("Error reporting settings are unavailable."); });
    return () => { active = false; };
  }, []);
  async function change(enabled) {
    setBusy(true);
    setMessage("");
    try { setStatus(await send("PUT", "/api/diagnostics/reporting", { enabled })); }
    catch { setMessage("Could not save error reporting preference. Try again."); }
    finally { setBusy(false); }
  }
  return html`<section class="settings-section" aria-label="Automatic error reports">
    <h3>Error reports</h3>
    <label class="settings-field" for="automatic-error-reports">
      <span>Send automatic error reports</span>
      <input id="automatic-error-reports" type="checkbox" checked=${!!status?.enabled}
          disabled=${!status || busy} onchange=${event => change(event.target.checked)} />
    </label>
    <p class="settings-note">Helps fix crashes and failed operations. Reports include technical
      error details with personal paths and secrets removed. No screenshots or gameplay recordings.</p>
    ${status && !status.configured && html`<p class="settings-note">Automatic reporting is not configured in this build.</p>`}
    ${status?.configured && status?.enabled && html`<p class="settings-note">Reports send automatically in the background.</p>`}
    ${message && html`<p class="settings-note" role="status">${message}</p>`}
  </section>`;
}

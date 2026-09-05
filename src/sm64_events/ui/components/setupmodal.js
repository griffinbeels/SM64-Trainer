// src/sm64_events/ui/components/setupmodal.js — the first-run setup screen.
//
// Two steps over one small API (GET|PUT|POST|DELETE /api/setup,
// server/setup_api.py): which platform you practice on, then a checklist for
// that platform. Only the emulator pane exists today -- console-support's own
// N64 pane registers itself into SETUP_PANES later, which is why the pane is
// looked up through a registry rather than an if/else here.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";

const html = htm.bind(h);

// How often the emulator pane re-checks the layer's state while the modal is
// open, so a row flips from "todo" to "found" the moment he starts Project64
// without needing to close and reopen the modal.
const POLL_MS = 2000;

function ChecklistRow({ status, title, children }) {
  return html`<div class=${`setup-row setup-row-${status}`}>
    <div class="setup-row-head">
      <span class="setup-status-dot" aria-hidden="true"></span>
      <span class="setup-row-title">${title}</span>
    </div>
    ${children != null && html`<div class="setup-row-body">${children}</div>`}
  </div>`;
}

// The three lines a fresh install writes, and why -- named exactly (the DLL,
// the registry setting, the ini) so the consent card states the whole action
// rather than gesturing at "some files".
function ConsentCard({ emu, installing, installError, onInstall }) {
  const pluginDir = emu.pj64_dir ? `${emu.pj64_dir}\\Plugin` : "the Plugin folder";
  const blockingReason = (emu.problems || [])[0] || null;
  const reason = installError || blockingReason;
  const disabled = installing || !!blockingReason;
  return html`<div class="setup-consent-card">
    <p>Installing writes three things:</p>
    <ul class="setup-writes">
      <li>the file <code class="setup-name">sm64_trainer_gfx.dll</code> into
        <code class="setup-path">${pluginDir}</code></li>
      <li>Project64's <code class="setup-name">Graphics Dll</code> setting, so it loads that file</li>
      <li>a small <code class="setup-name">sm64_trainer_gfx.ini</code> naming your current
        plugin${emu.wrapped_name ? html`, <code class="setup-name">${emu.wrapped_name}</code>` : ""},
        which keeps doing all the drawing</li>
    </ul>
    <p>Your recording's frames become game frames, so the input timeline is
      exact on every frame instead of estimated. Remove it any time from this
      row; Project64 must be closed to install or remove.</p>
    <button type="button" class="primary-button" disabled=${disabled}
        onclick=${onInstall}>
      ${installing ? "Installing…" : "Install the capture layer"}
    </button>
    ${reason && html`<p class="setup-disabled-reason">${reason}</p>`}
  </div>`;
}

function CaptureLayerRow({ setup, refresh }) {
  const emu = setup.emu;
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState(null);

  async function install() {
    setInstallError(null);
    setInstalling(true);
    try {
      await send("POST", "/api/setup/capture-layer", { consent: true });
    } catch (error) {
      setInstallError(error.message || String(error));
    }
    setInstalling(false);
    refresh();
  }

  async function remove() {
    try { await send("DELETE", "/api/setup/capture-layer"); }
    catch { /* the row re-reads state on the next refresh either way */ }
    refresh();
  }

  // A problem on a loaded layer (no picture reaches it; a newer build is
  // waiting) shows where the click lands -- "Active" alone read as fine
  // while every picture was refused (2026-09-05).
  const problems = emu.problems || [];
  const stale = emu.wrapper_current === false;
  const problemLines = problems.map((problem) =>
    html`<p class="setup-row-detail setup-row-problem">${problem}</p>`);
  const updateButton = stale && html`<button type="button" class="primary-button"
      disabled=${installing} onclick=${install}>${installing ? "Updating…" : "Update"}</button>`;
  if (emu.state === "active") {
    return html`<${ChecklistRow} status=${problems.length ? "warn" : "ok"}
        title="Frame-exact capture">
      <div class="setup-active-line">
        <${Icon} name="check" size=${14} /><span>Active</span>
        ${updateButton}
        <button type="button" onclick=${remove}>Remove</button>
      </div>
      ${problemLines}
      ${installError && html`<p class="setup-disabled-reason">${installError}</p>`}
    <//>`;
  }
  if (emu.state === "needs_restart") {
    return html`<${ChecklistRow} status="warn" title="Frame-exact capture">
      <p class="setup-row-detail">Installed. Restart Project64 to load it.</p>
      ${problemLines.slice(1)}
      ${updateButton}
      <button type="button" onclick=${remove}>Remove</button>
      ${installError && html`<p class="setup-disabled-reason">${installError}</p>`}
    <//>`;
  }
  if (emu.state === "regressed") {
    return html`<${ChecklistRow} status="warn" title="Frame-exact capture">
      <p class="setup-row-detail">${(emu.problems || [])[0]
        || "Project64 now names a different plugin."}</p>
      <button type="button" class="primary-button" disabled=${installing}
          onclick=${install}>${installing ? "Installing…" : "Re-install"}</button>
    <//>`;
  }
  if (emu.state === "unavailable") {
    return html`<${ChecklistRow} status="warn" title="Frame-exact capture">
      <p class="setup-row-detail">${(emu.problems || [])[0]
        || "Not available on this build."}</p>
    <//>`;
  }
  return html`<${ChecklistRow} status="todo" title="Frame-exact capture">
    <${ConsentCard} emu=${emu} installing=${installing}
        installError=${installError} onInstall=${install} />
  <//>`;
}

export function EmuSetupPane({ setup, refresh }) {
  const emu = setup.emu;
  const [gameMode, setGameMode] = useState(null);
  useEffect(() => {
    send("GET", "/api/mode").then(setGameMode).catch(() => setGameMode(null));
  }, []);

  const detected = emu.pj64_running && gameMode
    ? gameMode.effective.toUpperCase() : null;

  return html`<div class="setup-checklist">
    <${ChecklistRow} status=${emu.pj64_dir ? "ok" : "todo"} title="Project64">
      ${emu.pj64_dir
        ? html`<p class="setup-row-detail">Found at
            <code class="setup-path">${emu.pj64_dir}</code></p>`
        : html`<p class="setup-row-detail">Start Project64 once so the
            trainer can find it.</p>`}
    <//>
    <${ChecklistRow} status=${detected ? "ok" : "todo"} title="Usamune ROM">
      ${detected
        ? html`<p class="setup-row-detail">Detected: ${detected}</p>`
        : html`<p class="setup-row-detail">Not detected. Open the Usamune 1.93
            US ROM in Project64.</p>`}
    <//>
    <${CaptureLayerRow} setup=${setup} refresh=${refresh} />
  </div>`;
}

export function N64SetupPane() {
  return html`<p class="setup-n64-note">N64 setup arrives with console support.</p>`;
}

export const SETUP_PANES = { emu: EmuSetupPane, n64: N64SetupPane };

const PLATFORM_CHOICES = [
  ["emu", "Emulator", "Project64 on this PC"],
  ["n64", "N64", "A real console, capture card"],
];

export function SetupModal({ onClose, initialPlatform, initialPane }) {
  const [setup, setSetup] = useState(null);
  const [platform, setPlatform] = useState(initialPlatform || initialPane || null);
  const [error, setError] = useState(null);

  const refresh = () => getJSON("/api/setup").then(setSetup).catch(() => {});

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    // Nothing picked yet (no initialPlatform/initialPane) -- follow whatever
    // the server already has on file, once it answers.
    if (platform === null && setup) setPlatform(setup.platform);
  }, [setup]);

  async function choosePlatform(next) {
    setError(null);
    try {
      setSetup(await send("PUT", "/api/setup/platform", { platform: next }));
      setPlatform(next);
    } catch (submitError) {
      setError(submitError.message || String(submitError));
    }
  }

  const Pane = platform ? SETUP_PANES[platform] : null;

  return html`<${Modal} title="Set up practice" description="Practice on:"
      icon="settings" size="large" onClose=${onClose}
      footer=${html`<button type="button" onclick=${onClose}>Not now</button>`}>
    <div class="setup-modal">
      <div class="setup-platform-picks">
        ${PLATFORM_CHOICES.map(([key, name, detail]) => html`<button
            type="button" key=${key}
            class=${`setup-platform-choice ${platform === key ? "is-selected" : ""}`}
            onclick=${() => choosePlatform(key)}>
          <span class="setup-platform-name">${name}</span>
          <span class="setup-platform-detail">${detail}</span>
        </button>`)}
      </div>
      ${error && html`<p class="setup-error">${error}</p>`}
      ${Pane && setup && html`<${Pane} setup=${setup} refresh=${refresh} />`}
    </div>
  <//>`;
}

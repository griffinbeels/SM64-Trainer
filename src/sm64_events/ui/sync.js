// src/sm64_events/ui/sync.js — the version-sync coverage dashboard, served at
// /ui/sync.html.
//
// One card per FEATURE in `sync.gates.FEATURES` order, each holding every
// gate that feature owns and a US/JP status chip per gate. `GET /api/sync`
// (server/sync_api.py) is the only fetch this page ever makes; every later
// update arrives over the SAME `/ws/events` socket the main app listens on,
// as a `sync_verdict` broadcast -- so ticking a gate off from the runner
// (`tools/sync_version.py`) or from a second open tab updates this page with
// no reload, the live half `tests/test_ui_sync_page.py` exists to prove.
//
// This page owns NO opinion about which gates exist -- that is
// `sync/address_gates.py` / `calibration_gates.py` / `feature_gates.py`'s
// job, filled in by their own tracks. An empty registry (true of this
// worktree today, before those land) is a real, expected state, not an
// error: the header still renders and the body says so plainly instead of
// drawing eleven empty cards nobody asked to see.
import { h, render } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { RegionFlag } from "./components/regionflag.js";

const html = htm.bind(h);

// Mirrors `sync/gates.py::Gate.id`'s own convention -- "address.global_timer",
// "calibration.igt_clock.DISPLAY_TICK", "feature.star_grab.ground" -- the
// first segment is the KIND and already has its own chip, so the readable
// name is everything after it.
function idTail(id) {
  const dot = id.indexOf(".");
  return dot === -1 ? id : id.slice(dot + 1);
}

// "what we read" comes from the server (`sync/gates.py::reads_label`): the
// layout row's decomp symbol for an address, the constant a calibration
// backs, the event type a feature waits for -- ONE derivation, so this page
// cannot disagree with the runner about what a gate reads.
function whatWeRead(gate) {
  return gate.reads || idTail(gate.id);
}

function verdictFor(reports, version, gateId) {
  return reports[version][gateId] || { status: "missing" };
}

function hex(value) {
  return `0x${(value >>> 0).toString(16).padStart(8, "0")}`;
}

function StatusChip({ verdict }) {
  const text = verdict.value != null ? hex(verdict.value) : verdict.status;
  const title = verdict.measured ? JSON.stringify(verdict.measured) : verdict.status;
  return html`<span class="sync-chip" data-status=${verdict.status} title=${title}>${text}</span>`;
}

function GateRow({ gate, reports }) {
  const us = verdictFor(reports, "us", gate.id);
  const jp = verdictFor(reports, "jp", gate.id);
  const evidence = [us.evidence, jp.evidence].filter(Boolean);
  const title = `${gate.instruction} — proves: ${gate.proves}`
    + (evidence.length ? ` — ${evidence.join(" · ")}` : "");
  // A failed CALIBRATION is the one row where the two numbers being compared
  // are the finding itself -- the chip alone would hide exactly the thing a
  // human needs to read to decide what to do next.
  const showMeasured = (side) => gate.kind === "calibration"
    && side.status === "failed" && side.measured;
  return html`<tr title=${title}>
    <td class="sync-gate-id">${idTail(gate.id)}</td>
    <td><span class="sync-kind-chip">${gate.kind}</span></td>
    <td class="sync-reads">${whatWeRead(gate)}</td>
    <td>
      <${StatusChip} verdict=${us} />
      ${showMeasured(us) && html`<span class="sync-measured">${JSON.stringify(us.measured)}</span>`}
    </td>
    <td>
      <${StatusChip} verdict=${jp} />
      ${showMeasured(jp) && html`<span class="sync-measured">${JSON.stringify(jp.measured)}</span>`}
    </td>
  </tr>`;
}

function coverage(gates, reports, version) {
  const verified = gates.filter(
    (gate) => verdictFor(reports, version, gate.id).status === "verified").length;
  return { verified, total: gates.length };
}

function CoverageBar({ version, verified, total }) {
  const pct = total > 0 ? Math.round((verified / total) * 100) : 0;
  return html`<div class="sync-bar-wrap">
    <span class="sync-bar-label"><${RegionFlag} version=${version} size=${20} /></span>
    <span class="sync-bar-track"><span class="sync-bar" style=${`width:${pct}%`} /></span>
    <span class="sync-bar-count">${verified}/${total}</span>
  </div>`;
}

function FeatureCard({ feature, gates, reports }) {
  const us = coverage(gates, reports, "us");
  const jp = coverage(gates, reports, "jp");
  return html`<section class="sync-card" data-feature=${feature}>
    <div class="sync-card-head">
      <h2>${feature}</h2>
      <div class="sync-bars">
        <${CoverageBar} version="us" verified=${us.verified} total=${us.total} />
        <${CoverageBar} version="jp" verified=${jp.verified} total=${jp.total} />
      </div>
    </div>
    ${gates.length > 0 && html`<div class="sync-table-wrap">
      <table class="sync-table">
        <thead><tr>
          <th>Gate</th><th>Kind</th><th>Reads</th><th>US</th><th>JP</th>
        </tr></thead>
        <tbody>
          ${gates.map((gate) => html`<${GateRow} key=${gate.id} gate=${gate} reports=${reports} />`)}
        </tbody>
      </table>
    </div>`}
  </section>`;
}

function Dashboard() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetch("/api/sync").then((response) => response.json()).then(setData);
  }, []);

  useEffect(() => {
    // Origin-relative, same as store.js's own connection -- the server's
    // port moves between a dev run and run-test-server.bat, so anything
    // hardcoded here would be wrong the moment it did.
    const socket = new WebSocket(`ws://${location.host}/ws/events`);
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type !== "sync_verdict") return;
      const { version, gate_id: gateId, verdict, at } = message.payload;
      setData((prior) => {
        if (!prior) return prior;
        return { ...prior, reports: { ...prior.reports,
          [version]: { ...prior.reports[version], [gateId]: { ...verdict, at } } } };
      });
    };
    return () => socket.close();
  }, []);

  if (!data) return html`<div class="sync-page"><p>Loading…</p></div>`;

  const { features, gates, reports } = data;
  const overall = { us: coverage(gates, reports, "us"), jp: coverage(gates, reports, "jp") };

  return html`<div class="sync-page">
    <div class="sync-head">
      <h1>Version sync — US vs JP</h1>
      <p>Every version-dependent thing the tracker relies on, and whether each
        ROM has a verified reading for it.</p>
      ${gates.length > 0 && html`<div class="sync-overall">
        <span>US <b>${overall.us.verified}/${overall.us.total}</b> verified</span>
        <span>JP <b>${overall.jp.verified}/${overall.jp.total}</b> verified</span>
      </div>`}
    </div>
    ${gates.length === 0
      ? html`<p class="sync-empty-registry">no gates registered</p>`
      : features.map((feature) => html`<${FeatureCard} key=${feature} feature=${feature}
          gates=${gates.filter((gate) => gate.feature === feature)} reports=${reports} />`)}
  </div>`;
}

render(html`<${Dashboard} />`, document.getElementById("root"));

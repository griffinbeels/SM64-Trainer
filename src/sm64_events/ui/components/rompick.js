// src/sm64_events/ui/components/rompick.js -- the Ghost Lab's one-time ROM step (Ghost Lab beta, D1 and D6).
//
// RomPick({ region, onFound }) shows whether the Trainer has a private copy of that region's Usamune ROM
// (GET /api/scene-lab/roms, server/lab_roms_api.py over scenelab/roms.py). While it has none, it shows the
// server's one sentence naming the exact ROM, asks again every 2 s (each ask also copies the game when
// Project64 runs the right one, so starting the game is enough), and offers "Choose ROM file", which sends the
// chosen file to this computer's own Trainer server (POST /api/scene-lab/roms/pick) and nowhere else.
// A file of the other region is kept too, and the step says it still needs this one.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";

const html = htm.bind(h);
const ROOT = "/api/scene-lab/roms";
const POLL_MS = 2000;
// The server reads at most this much before it refuses a file as too large (lab_roms_api.MAX_BODY), so a
// larger file is cut here and never streams whole to it.
const MAX_SENT = 16 * 1024 * 1024 + 1;
const FROM = {
  game: "The Trainer copied it from the game running in Project64.",
  folder: "The Trainer found it in Project64's ROM folder.",
  picked: "It came from the file you chose.",
};
const PRIVACY = "The Trainer keeps a private copy on this computer and never uploads it.";

if (typeof document !== "undefined" && !document.querySelector("link[data-rom-pick]")) {
  const sheet = Object.assign(document.createElement("link"), { rel: "stylesheet", href: "/ui/rompick.css" });
  sheet.dataset.romPick = "";
  document.head.append(sheet);
}

export function RomPick({ region = "us", onFound } = {}) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const input = useRef(null);
  const entry = status?.regions?.[region];
  const found = Boolean(entry?.found);

  useEffect(() => {
    let live = true;
    let timer = null;
    const ask = () => {
      if (typeof document !== "undefined" && document.hidden) { timer = setTimeout(ask, POLL_MS); return; }
      getJSON(ROOT).then((answer) => {
        if (!live) return;
        setStatus(answer);
        if (!answer.regions?.[region]?.found) timer = setTimeout(ask, POLL_MS);
      }).catch(() => { if (live) timer = setTimeout(ask, POLL_MS); });
    };
    ask();
    return () => { live = false; clearTimeout(timer); };
  }, [region, found]);

  useEffect(() => { if (found && onFound) onFound(entry); }, [found]);

  const choose = (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setProblem("");
    const body = file.size > MAX_SENT ? file.slice(0, MAX_SENT) : file;
    getJSON(`${ROOT}/pick?region=${region}`,
      { method: "POST", body, headers: { "Content-Type": "application/octet-stream" } })
      .then((answer) => {
        setStatus(answer);
        const wanted = answer.regions?.[region];
        if (!wanted?.found) {
          const other = answer.regions?.[answer.stored];
          setProblem(`That file is ${other?.label ?? "another ROM"}. The Trainer kept it too, but this step still needs ${wanted?.label}.`);
        }
      })
      .catch((error) => setProblem(error.message))
      .finally(() => setBusy(false));
  };

  if (!entry) {
    return html`<section class="rom-pick" data-region=${region} data-state="loading" aria-busy="true">
      <p class="rom-pick-line">Looking for the game file…</p></section>`;
  }
  if (found) {
    return html`<section class="rom-pick" data-region=${region} data-state="found">
      <p class="rom-pick-line rom-pick-found"><span aria-hidden="true">✓ </span>${entry.label} found.</p>
      <p class="rom-pick-detail">${FROM[entry.source] ?? ""} ${PRIVACY}</p></section>`;
  }
  return html`<section class="rom-pick" data-region=${region} data-state=${busy ? "checking" : "missing"}>
    <p class="rom-pick-line rom-pick-reason">${entry.reason}</p>
    <p class="rom-pick-detail">${PRIVACY}</p>
    <div class="rom-pick-actions">
      <button type="button" class="primary-button" disabled=${busy} onclick=${() => input.current?.click()}>
        ${busy ? "Checking the file…" : "Choose ROM file"}</button>
      <input ref=${input} type="file" accept=".z64,.n64,.v64,.rom,.bin" hidden onchange=${choose} />
    </div>
    ${problem ? html`<p class="rom-pick-problem" role="alert">${problem}</p>` : null}
  </section>`;
}

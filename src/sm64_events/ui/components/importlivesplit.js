// src/sm64_events/ui/components/importlivesplit.js — your LiveSplit golds.
//
// A gold is the fastest a split has ever been, which is proof of the best you
// have ever done one piece of the run — exactly the thing a practicer already
// has and this tool did not.
//
// TWO RULES SHOW UP IN THE COPY, because both would otherwise read as bugs.
// A gold is a REAL-TIME stretch of the run, so it lands on a segment you built
// here and never on a star, whose bests are Usamune IGT — a real-time split
// filed against an IGT ladder reads as a wildly good time. And splits are
// matched by NAME, so what does not match is named back rather than counted
// away: a file of thirty splits that lands four owes you twenty-six answers.
//
// It PREVIEWS first, like the paste door, through the same planner the button
// then performs.
import { h } from "preact";
import { useRef, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";

const html = htm.bind(h);

const REASONS = {
  not_a_segment: "this names a star — a gold is a real-time split, so it can "
    + "only land on a segment",
  unknown_target: "no segment here is called this",
};

export function ImportLiveSplit({ onDone }) {
  const input = useRef(null);
  const [name, setName] = useState("");
  const [data, setData] = useState(null);
  const [preview, setPreview] = useState(null);
  const [phase, setPhase] = useState("idle");   // idle | ready | working
                                                // | done | error
  const [error, setError] = useState("");

  async function post(body, dryRun) {
    // Raw bytes, not multipart: the server takes the request body itself
    // rather than pulling `python-multipart` into the frozen exe for one
    // field (the same call `server/compare_api.py`'s upload already makes).
    const reply = await fetch(
      `/api/import/livesplit${dryRun ? "?dry_run=true" : ""}`,
      { method: "POST", body,
        headers: { "Content-Type": "application/octet-stream" } });
    const payload = await reply.json().catch(() => null);
    if (!reply.ok) {
      throw new Error((payload && payload.detail) || `${reply.status}`);
    }
    return payload;
  }

  async function pick(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    setName(file.name);
    setError("");
    setPhase("working");
    try {
      const bytes = await file.arrayBuffer();
      setData(bytes);
      setPreview(await post(bytes, true));
      setPhase("ready");
    } catch (err) {
      setError(err.message);
      setPhase("error");
    }
  }

  async function run() {
    setPhase("working");
    try {
      const body = await post(data, false);
      setPreview(body);
      setPhase("done");
      if (body.imported && onDone) onDone(body);
    } catch (err) {
      setError(err.message);
      setPhase("error");
    }
  }

  const rejected = (preview && preview.rejected) || [];
  return html`<section class="settings-section importlivesplit">
    <div class="settings-section-head">
      <div>
        <h3>Import your LiveSplit golds</h3>
        <p>Your best-ever time for each split, filed against the segments you
          have built here.</p>
      </div>
    </div>
    <div class="importpaste-row">
      <input ref=${input} type="file" accept=".lss,application/xml,text/xml"
          class="importlivesplit-file" onchange=${pick} />
      ${phase === "ready" && html`<button type="button" class="primary-button"
          disabled=${!preview.imported} onclick=${run}>
        Import ${preview.imported}
      </button>`}
      ${phase === "done" && html`<span class="settings-note is-ok">${
        preview.imported}${" "}gold${preview.imported === 1 ? "" : "s"} added.</span>`}
    </div>
    ${name && html`<p class="settings-note">${name}</p>`}
    ${phase === "ready" && preview.imported === 0 && html`<p
      class="settings-note">Nothing in this file beats what you already
      have.</p>`}
    ${rejected.length > 0 && html`<div class="importpaste-rejects">
      <p class="settings-note is-bad">${rejected.length}${" "}
        split${rejected.length === 1 ? "" : "s"} did not land:</p>
      <ul>
        ${rejected.slice(0, 12).map((row) => html`<li key=${row.line}>
          <code>${row.text}</code>
          <span class="meta">${REASONS[row.reason] || row.reason}</span>
        </li>`)}
      </ul>
      ${rejected.length > 12 && html`<p class="settings-note">…and${" "}
        ${rejected.length - 12} more.</p>`}
    </div>`}
    ${phase === "error" && html`<p class="settings-note is-bad">${error}</p>`}
    ${phase === "done" && preview.imported > 0
      && html`<button type="button" class="quiet-button importpaste-undo"
          onclick=${async () => {
            const reply = await fetch("/api/import/livesplit",
                                      { method: "DELETE" });
            const body = await reply.json();
            setPreview({ ...preview, imported: 0 });
            setPhase("idle");
            if (onDone) onDone(body);
          }}>
        <${Icon} name="trash" size=${13} />${" "}Undo this import
      </button>`}
  </section>`;
}

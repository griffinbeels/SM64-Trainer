// SAVE AS GHOST: race this exact run, whether or not it is a PB or a template.
// His ask, 2026-09-22: "a ghost might not necessarily be the template we
// want." Saving keeps the run's Mario poses and ticks it in the practice
// card's Ghosts row (`ghost/saved.py`); the input comparison is untouched.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

export function SaveGhostButton({ attemptId }) {
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const url = `/api/saved-ghosts/${attemptId}`;
  useEffect(() => {
    let alive = true;
    getJSON(url).then((answer) => { if (alive) setSaved(answer.available ? Boolean(answer.saved) : null); })
      .catch(() => { if (alive) setSaved(null); });  // no ghost session: no button
    return () => { alive = false; };
  }, [url]);
  if (saved === null) return null;

  async function toggle() {
    setBusy(true); setNotice("");
    try {
      const answer = await send(saved ? "DELETE" : "POST", url);
      setSaved(Boolean(answer.saved));
      setNotice(!answer.saved ? "Removed from your ghosts."
        : answer.poses_reason ? `Saved as a ghost, but only the whole picture can show it: ${answer.poses_reason}`
        : "Saved as a ghost. It is ticked in the Ghosts row.");
    } catch (error) {
      setNotice(`Ghost not saved: ${error.message}`);
    } finally {
      setBusy(false);
    }
  }

  return html`<button class="save-ghost" onclick=${toggle} disabled=${busy}
      title=${saved ? "Stop offering this run as a ghost" : "Race this run as a ghost, without making it your template"}>
      ${saved ? "Remove ghost" : "Save as ghost"}</button>
    ${notice && html`<span class="save-ghost-note" role="status">${notice}</span>`}`;
}

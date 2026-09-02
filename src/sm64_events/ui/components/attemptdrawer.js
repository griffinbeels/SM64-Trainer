// src/sm64_events/ui/components/attemptdrawer.js
// What opens under an attempt row: the clip, and the inputs, on ONE clock.
//
// The video is the clock whenever there is one -- the timeline follows the
// element rather than running a second clock beside it. That is why this
// wrapper exists at all: somebody has to own the element both halves read,
// and neither half should own the other.
//
// An attempt with no saved clip still gets its timeline. The video half is
// simply absent, which is the honest rendering of "there is no footage",
// rather than a drawer that refuses to open.
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { ReplayPlayer } from "./replay.js";
import { InputTimeline } from "./inputtimeline.js";
import { clipClock as buildClipClock } from "../frame.js";

const html = htm.bind(h);

export function AttemptDrawer({ attemptId, onCompare, onTemplateMarked }) {
  const [video, setVideo] = useState(null);
  // Where the attempt's anchor sits inside the clip (the replay pre-pad,
  // measured from the clip's own first frame by the server). The timeline
  // shifts by it so clip time and track frame are one axis.
  const [anchorOffsetS, setAnchorOffsetS] = useState(0);
  // The clip's own frame map (which game frame each video frame shows) and
  // its encode rate -- the timeline's exact clock; the offset is its fallback.
  const [clipClock, setClipClock] = useState({ frameMap: null,
                                              clock: null,
                                              padReading: null });
  // The timeline appears only once the replay has ANSWERED (his ruling
  // 2026-09-01: "it should be hidden until we extract the replay, after
  // which it's shown") -- extraction is where the map is read off the
  // footage, so a timeline drawn before it is a track nobody has checked,
  // and its header changed under him once the clip arrived. A clip that
  // cannot be cut is an answer too: the timeline then shows unchecked.
  const [replaySettled, setReplaySettled] = useState(false);
  const [marking, setMarking] = useState(null);   // null | "busy" | a message

  async function markTemplate() {
    setMarking("busy");
    try {
      const response = await fetch(
        `/api/attempts/${attemptId}/inputs/template`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}) });
      const text = await response.text();
      if (!response.ok) { setMarking(text || "could not mark this attempt"); return; }
      setMarking("marked");
      if (onTemplateMarked) onTemplateMarked();
    } catch (error) {
      setMarking(String(error));
    }
  }

  return html`<div class="attempt-drawer">
    <${ReplayPlayer} attemptId=${attemptId} onCompare=${onCompare}
        onVideoEl=${setVideo}
        onView=${(view) => {
          if (view) {
            setAnchorOffsetS(view.anchor_offset_s || 0);
            setClipClock({ frameMap: view.frame_map || null,
                           clock: buildClipClock(view),
                           padReading: view.pad_reading || null });
          }
          setReplaySettled(true);
        }} />
    <div class="attempt-drawer-inputs">
      ${replaySettled
        ? html`<${InputTimeline} attemptId=${attemptId} video=${video}
              anchorOffsetS=${anchorOffsetS}
              frameMap=${clipClock.frameMap} clock=${clipClock.clock}
              padReading=${clipClock.padReading} />`
        : html`<div class="input-timeline-waiting">The input timeline appears
            once the replay is cut and checked against its footage.</div>`}
      <div class="attempt-drawer-tools">
        <button onclick=${markTemplate} disabled=${marking === "busy"}
            title="Compare every future run against THIS one">
          <${Icon} name="bookmark" size=${14} />
          ${marking === "marked" ? "This is your template" : "Make this my template"}
        </button>
        <a class="button-link"
           href=${`/api/attempts/${attemptId}/inputs/document`}
           target="_blank" rel="noopener"
           title="The inputs as a text file you can keep, edit, or send someone">
          <${Icon} name="save" size=${14} /> Export inputs
        </a>
        ${marking && marking !== "busy" && marking !== "marked"
          && html`<span class="is-error">${marking}</span>`}
      </div>
    </div>
  </div>`;
}

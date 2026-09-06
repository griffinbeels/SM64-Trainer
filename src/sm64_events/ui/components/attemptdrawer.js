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
import { InputTemplates } from "./inputtemplates.js";
import { ReplayPlayer } from "./replay.js";
import { InputTimeline } from "./inputtimeline.js";
import { clipClock as buildClipClock } from "../frame.js";

const html = htm.bind(h);

export function AttemptDrawer({ attemptId, imported = false, onCompare, onTemplateMarked, targetLabel }) {
  const [video, setVideo] = useState(null);
  // Where the attempt's anchor sits inside the clip (the replay pre-pad,
  // measured from the clip's own first frame by the server). The timeline
  // shifts by it so clip time and track frame are one axis.
  const [anchorOffsetS, setAnchorOffsetS] = useState(0);
  // The clip's own frame map (which game frame each video frame shows) and
  // its encode rate -- the timeline follows only the retained association.
  const [clipClock, setClipClock] = useState({ frameMap: null,
                                              clock: null,
                                              padAgreement: null,
                                              frameMapSource: null,
                                              inputAlignment: null });
  // The timeline appears only once the replay has ANSWERED (his ruling
  // 2026-09-01: "it should be hidden until we extract the replay, after
  // which it's shown") -- extraction is where the map is read off the
  // footage, so a timeline drawn before it is a track nobody has checked,
  // and its header changed under him once the clip arrived. A clip that
  // cannot be cut is an answer too: the timeline then shows unchecked.
  const [replaySettled, setReplaySettled] = useState(false);

  return html`<div class="attempt-drawer">
    <${ReplayPlayer} attemptId=${attemptId} imported=${imported} onCompare=${onCompare}
        onVideoEl=${setVideo}
        onView=${(view) => {
          if (view) {
            setAnchorOffsetS(view.anchor_offset_s || 0);
            setClipClock({ frameMap: view.frame_map || null,
                           pictureIgt: view.picture_igt || null,
                           clock: buildClipClock(view),
                           padAgreement: view.pad_stamp_agreement || null,
                           frameMapSource: view.frame_map_source || null,
                           inputAlignment: view.input_alignment || null });
          }
          setReplaySettled(true);
        }} />
    ${!imported && html`<div class="attempt-drawer-inputs">
      ${replaySettled
        ? html`<${InputTimeline} attemptId=${attemptId} video=${video}
              anchorOffsetS=${anchorOffsetS}
              frameMap=${clipClock.frameMap} clock=${clipClock.clock}
              pictureIgt=${clipClock.pictureIgt}
              padAgreement=${clipClock.padAgreement}
              frameMapSource=${clipClock.frameMapSource}
              inputAlignment=${clipClock.inputAlignment}
              tools=${(data) => html`<${InputTemplates} attemptId=${attemptId}
                  data=${data} targetLabel=${targetLabel} onTemplateMarked=${onTemplateMarked} />`} />`
        : html`<div class="input-timeline-waiting">The input timeline appears
            once the replay is cut and checked against its footage.</div>`}
    </div>`}
  </div>`;
}

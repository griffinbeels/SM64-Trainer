// src/sm64_events/ui/components/inputtimeline.js
// One attempt's inputs, drawn as lanes over frames -- and, when a template is
// active, that template drawn BEHIND them so the gap is something you look at.
//
// It reports what each track was doing on a frame and names nothing as the
// reason. His ruling, 2026-08-20: "I think the user can deduce the corrections
// they need to make based on the data. If we try to prescribe solutions,
// that's a totally different problem." So there are no generated corrections,
// and -- one level deeper -- no attempt to MATCH your presses to the
// template's either: deciding "this A corresponds to that A" is an inference,
// and a wrong match is a confidently wrong number wearing a measurement's
// clothes.
//
// Lanes are HTML boxes at percentage widths rather than SVG, so they stretch
// with the drawer at any width with no aspect arithmetic. The stick is the one
// SVG, and it sets preserveAspectRatio explicitly -- the default is `slice`,
// which CROPS whatever the container's aspect does not cover.
import { h } from "preact";
import { useRef, useState } from "preact/hooks";
import htm from "htm";
import { useOverlayRows } from "../inputpreferences.js";
import { TimelineReviewControls } from "../timelinereview.js";
import { TimelineScroll } from "../timelinescroll.js";
import { ReviewReadout } from "../reviewreadout.js";
import { useTimelineData, useTimelinePicture, useTimelineReview,
         useTimelineModel, useTimelineSeek } from "./inputtimelinehooks.js";
import { useTimelineSelection } from "./inputtimelineinteraction.js";
import { useStaticTimelineLanes, TimelineTrack } from "./inputtimelinelanes.js";
import { TimelineStatus, TimelineHeader, TemplateNote, OverlayControls, TimelineSetup } from "./inputtimelinechrome.js";
import { TimelineInspector } from "./inputtimelineinspector.js";

export { frameAt, actionAt, momentAt, CORE_BUTTONS, lanesOf, contiguousRuns,
         frameAtTime, timeAtFrame, trackFrameOf, gameFrameOf, mappedFrameAtTime,
         mappedTimeAtFrame, mappedLoopWindow, loopFromFrames, inspectorClock } from "./inputtimelinemodel.js";

const html = htm.bind(h);

export function InputTimeline({ attemptId, video, anchorOffsetS = 0,
                                frameMap = null, clock = null, inputSpan,
                                pictureIgt = null,
                                padAgreement = null,
                                frameMapSource = null,
                                inputAlignment = null,
                                reviewState = null, onReviewState = null,
                                compact = false,
                                tools = null }) {
  const { state, retry } = useTimelineData(attemptId, inputSpan, frameMap);
  const data = state.phase === "ready" ? state.data : null;
  const { frame, presentedSlot, setFrame } = useTimelinePicture(video, data, frameMap, clock);
  const { review, reviewLoading, updateReview } = useTimelineReview(reviewState, onReviewState);
  const model = useTimelineModel(data, review, onReviewState, video, frameMap, clock);
  const { total, templateKey, view, boundedClock } = model;
  const [overlayVisible, toggleOverlay] = useOverlayRows();
  // Both pointer gestures and the playhead measure the track column, excluding labels.
  const trackColumn = useRef(null);
  const { seek, seekSlot, slotCount } = useTimelineSeek({ video, clock, frameMap, boundedClock, total, data, setFrame });
  const selection = useTimelineSelection({ trackColumn, view, video, frameMap, boundedClock,
    data, total, reviewLoading, updateReview, seek });
  const [checkOpen, setCheckOpen] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const staticLanes = useStaticTimelineLanes({ ...model, data, overlayVisible, seek });
  const shiftTemplate = (next) => {
    if (!templateKey) return;
    updateReview({ template_offsets: { ...review.template_offsets,
      [templateKey]: Math.max(-1000000, Math.min(1000000, Math.round(next))) } });
  };
  if (!data || !data.runs.length) return html`<${TimelineStatus} state=${state} retry=${retry} />`;

  return html`<${TimelineContent} data=${data} model=${model} selection=${selection}
    trackColumn=${trackColumn} state=${state} retry=${retry} padAgreement=${padAgreement}
    frameMapSource=${frameMapSource} inputAlignment=${inputAlignment} seek=${seek}
    seekSlot=${seekSlot} slotCount=${slotCount} frame=${frame} review=${review}
    reviewLoading=${reviewLoading} updateReview=${updateReview} shiftTemplate=${shiftTemplate}
    overlayVisible=${overlayVisible} toggleOverlay=${toggleOverlay} staticLanes=${staticLanes}
    video=${video} pictureIgt=${pictureIgt} presentedSlot=${presentedSlot} tools=${tools} compact=${compact}
    checkOpen=${checkOpen} setCheckOpen=${setCheckOpen} setupOpen=${setupOpen} setSetupOpen=${setSetupOpen} />`;
}

function TimelineContent({ data, model, selection, trackColumn, state, retry, padAgreement,
                           frameMapSource, inputAlignment, seek, seekSlot, slotCount, frame, review,
                           reviewLoading, updateReview, shiftTemplate, overlayVisible, toggleOverlay,
                           staticLanes, video, pictureIgt, presentedSlot, tools, compact,
                           checkOpen, setCheckOpen, setupOpen, setSetupOpen }) {
  const { total, lead, templateKey, offset, template, view, loopWindow } = model;
  const { selectedRange, selectionError, beginSelection, moveSelection, finishSelection } = selection;
  return html`<div class=${`input-timeline ${compact ? "is-compact" : ""}`}>
    <${TimelineHeader} data=${data} refreshError=${state.refreshError} retry=${retry}
      padAgreement=${padAgreement} frameMapSource=${frameMapSource} inputAlignment=${inputAlignment}
      seek=${seek} seekSlot=${seekSlot} slotCount=${slotCount}
      checkOpen=${checkOpen} setCheckOpen=${setCheckOpen} setupOpen=${setupOpen} setSetupOpen=${setSetupOpen} />
    <${TemplateNote} data=${data} offset=${offset} total=${total} lead=${lead} />
    <${TimelineReviewControls} templateKey=${templateKey} hasTemplate=${!!template}
        offset=${offset} view=${view} total=${total} frame=${frame} lead=${lead}
        loopWindow=${loopWindow} trackColumn=${trackColumn} loading=${reviewLoading}
        onShift=${shiftTemplate} onChange=${updateReview} />
    <${OverlayControls} template=${template} data=${data}
      overlayVisible=${overlayVisible} toggleOverlay=${toggleOverlay} />
    <div class="input-lanes" tabindex="0"
         title="Click to seek; drag to select a loop"
         onpointerdown=${beginSelection} onpointermove=${moveSelection}
         onpointerup=${finishSelection} onpointercancel=${finishSelection} onlostpointercapture=${finishSelection}
         role="group" aria-label="Input lanes">
      <${TimelineTrack} trackColumn=${trackColumn} selectedRange=${selectedRange} lead=${lead}
        view=${view} loopWindow=${loopWindow} review=${review} frame=${frame} />
      ${staticLanes}
    </div>
    <${TimelineScroll} view=${view} total=${total} trackColumn=${trackColumn}
      loading=${reviewLoading} onChange=${updateReview} />
    ${selectionError && html`<p class="replay-control-error" role="status">${selectionError}</p>`}
    <${ReviewReadout}>
      <${TimelineInspector} data=${data} template=${template} frame=${frame} lead=${lead}
        total=${total} video=${video} pictureIgt=${pictureIgt} presentedSlot=${presentedSlot} />
      ${typeof tools === "function" ? tools(data) : tools}
    </${ReviewReadout}>
    <${TimelineSetup} open=${setupOpen} onClose=${() => setSetupOpen(false)} />
  </div>`;
}

import { useCallback, useEffect, useMemo, useState } from "preact/hooks";
import { useTemplateRevision } from "../inputpreferences.js";
import { slotAtTime, timeOfSlot } from "../frame.js";
import { watchVideoPicture } from "../videopicture.js";
import { templateOnAxis, templateReviewKey, timelineWindow } from "../timelinereview.js";
import { stopShuttle } from "../replayshuttle.js";
import { pauseReviewSource, seekReviewSource } from "../reviewsource.js";
import { lanesOf, curvePath, speedPeak, speedPath, stickReach, stickPath, stampedRuns,
         mappedFrameAtTime, mappedTimeAtFrame, mappedLoopWindow } from "./inputtimelinemodel.js";

export function useTimelineData(attemptId, inputSpan, frameMap) {
  const [state, setState] = useState({ phase: "loading" });
  const templateRevision = useTemplateRevision();
  const [retry, setRetry] = useState(0);
  // The server excludes heartbeat copies from these bounds: an old held
  // picture remains in the video without stretching the input axis through
  // minutes of unrecorded pre-attempt time. Keep the raw-map fallback for
  // older servers that do not publish the captured span.
  const clipSpan = useMemo(() => {
    if (inputSpan !== undefined) return inputSpan ? inputSpan.join(",") : null;
    if (!frameMap || !frameMap.length) return null;
    let low = null;
    let high = null;
    for (const raw of frameMap) {
      if (raw == null) continue;
      if (low === null || raw < low) low = raw;
      if (high === null || raw > high) high = raw;
    }
    return low === null ? null : `${low},${high}`;
  }, [frameMap, inputSpan]);

  useEffect(() => {
    let alive = true;
    setState((old) => old.phase === "ready" && old.data.attempt_id === attemptId
      ? old : { phase: "loading" });
    const range = clipSpan
      ? `?from_frame=${clipSpan.split(",")[0]}&to_frame=${clipSpan.split(",")[1]}`
      : "";
    fetch(`/api/attempts/${attemptId}/inputs${range}`)
      .then((response) => (response.ok
        ? response.json()
        : response.text().then((text) => Promise.reject(new Error(text)))))
      .then((data) => { if (alive) setState({ phase: "ready", data }); })
      .catch((error) => { if (alive) setState((old) => old.phase === "ready"
        ? { ...old, refreshError: String(error) }
        : { phase: "error", error: String(error) }); });
    return () => { alive = false; };
  }, [attemptId, clipSpan, templateRevision, retry]);

  return { state, retry: () => setRetry((value) => value + 1) };
}

export function useTimelinePicture(video, data, frameMap, clock) {
  const [frame, setFrame] = useState(video ? null : 0);
  const [presentedSlot, setPresentedSlot] = useState(null);
  // ONE CLOCK, ALWAYS. The video is the clock whenever there is one: the
  // timeline reads it every frame and never keeps a position of its own, so
  // dragging the video's scrubber moves the playhead and dragging the
  // playhead seeks the video. There is no "stop following" state -- that
  // was how the two drifted apart (his report, 2026-08-22: "if I drag the
  // video playhead itself, it should automatically move the input playback
  // system's playhead as well. We need both of these to always stay in
  // sync").
  useEffect(() => {
    if (!video || !data) return undefined;
    const { frames, stretches } = data;
    const readAt = (seconds) => {
      const at = seconds == null ? null : mappedFrameAtTime(seconds,
        frameMap, clock, stretches, frames);
      setFrame((current) => (current === at ? current : at));
      setPresentedSlot(seconds == null ? null : slotAtTime(seconds, clock));
    };
    // ReplayPlayer starts observing before autoplay. A late mount or template
    // refresh therefore reuses the last delivered mediaTime even while paused.
    // No delivered picture means unknown, never the requested seek's time.
    return watchVideoPicture(video, readAt);
  }, [video, data, frameMap, clock]);

  return { frame, presentedSlot, setFrame };
}

export function useTimelineReview(reviewState, onReviewState) {
  const [localReview, setLocalReview] = useState({});
  const review = reviewState || localReview;
  const reviewLoading = !!onReviewState && reviewState == null;
  const updateReview = useCallback((patch) => {
    if (reviewLoading) return;
    if (onReviewState) onReviewState(patch);
    else setLocalReview((old) => ({ ...old, ...patch }));
  }, [reviewLoading, onReviewState]);
  return { review, reviewLoading, updateReview };
}

function timelineDuration(video, clock) {
  return Number.isFinite(video?.duration) ? video.duration : clock?.duration;
}

export function useTimelineModel(data, review, onReviewState, video, frameMap, clock,
                                 pictureStates = null, frameMapSource = null) {
  const duration = timelineDuration(video, clock);
  const boundedClock = useMemo(() => ({ ...clock, duration }), [clock, duration]);
  const total = data?.frames || 1;
  const lead = data?.lead_frames || 0;
  const templateKey = onReviewState && !data?.template?.source?.revision
    ? null : templateReviewKey(data?.template);
  const savedOffset = review.template_offsets?.[templateKey];
  const offset = Number.isInteger(savedOffset) ? savedOffset : 0;
  const template = useMemo(() => templateOnAxis(data?.template, lead, offset), [data, lead, offset]);
  const view = useMemo(() => timelineWindow(review.zoom, total), [review.zoom, total]);
  // What the lanes draw: the pictures' own stamps wherever a picture exists
  // (exact capture), the polled sample only where none does. Without exact
  // capture the polled track is all there is.
  const drawn = useMemo(() => {
    if (!data) return [];
    if (frameMapSource !== "plugin" || !Array.isArray(pictureStates)) return data.runs;
    return stampedRuns(data.runs, frameMap, pictureStates, data.stretches, total);
  }, [data, frameMap, pictureStates, frameMapSource, total]);
  const lanes = useMemo(
    () => (data ? lanesOf(drawn, data.buttons) : []), [data, drawn]);
  const templateLanes = useMemo(
    () => (template ? lanesOf(template.runs, data.buttons) : []), [data, template]);
  // A delivered picture only moves the inspector/playhead. Generating paths
  // visits every run, so retain this geometry until source/shift changes.
  const curves = useMemo(() => {
    if (!data) return {};
    const ghostRuns = template?.runs || [];
    const peak = speedPeak(drawn, ghostRuns);
    const reach = stickReach(data.stick_max, drawn, ghostRuns);
    const paths = (runs) => ({
      x: curvePath(runs, (group) => stickPath(group, "x", reach)),
      y: curvePath(runs, (group) => stickPath(group, "y", reach)),
      speed: curvePath(runs, (group) => speedPath(group, peak)),
    });
    // Polled fills draw as their own dashed segments so a stretch with no
    // picture never reads as the picture's own stick or speed.
    const polled = drawn.filter((run) => run.polled);
    return { mine: paths(drawn.filter((run) => !run.polled)), template: paths(ghostRuns),
             polled: polled.length ? paths(polled) : null };
  }, [data, drawn, template]);
  const loopWindow = useMemo(() => data ? mappedLoopWindow(review.loop, frameMap,
    boundedClock,
    data.stretches, total) : null, [review.loop, frameMap, boundedClock, data, total]);

  return { total, lead, templateKey, offset, template, view, lanes, templateLanes, curves, loopWindow, boundedClock };
}

export function useTimelineSeek({ video, clock, frameMap, boundedClock, total, data, setFrame }) {
  const slotCount = clock?.times?.length || frameMap?.length || 0;
  const seekSlot = useCallback((slot) => {
    stopShuttle(video);
    if (!Number.isInteger(slot) || slot < 0 || slot >= slotCount) return;
    pauseReviewSource(video);
    seekReviewSource(video, timeOfSlot(slot, boundedClock));
  }, [video, slotCount, boundedClock]);
  const seek = useCallback((next) => {
    stopShuttle(video);
    const clamped = Math.max(0, Math.min(total - 1, next));
    if (video) {
      // Seeking the video is how the timeline moves: the clock loop above
      // reads the new time back on the next frame, so the two cannot
      // disagree even for a frame.
      pauseReviewSource(video);
      const mapped = mappedTimeAtFrame(clamped, frameMap, boundedClock, data.stretches);
      if (mapped !== null) {
        seekReviewSource(video, mapped);
        return;
      }
      // An absent association cannot locate this input in the footage.
      // Playback still works; guessing an anchor offset would reintroduce
      // the desync that the presented-picture reader refuses to display.
    } else {
      setFrame(clamped);
    }
  }, [video, total, frameMap, boundedClock, data, setFrame]);
  return { seek, seekSlot: video ? seekSlot : null, slotCount };
}

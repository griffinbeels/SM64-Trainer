import { slotAtTime, timeOfSlot } from "../frame.js";
import { pictureInterval } from "../reviewmedia.js";
import { fmtIgtShort } from "../format.js";

export const STICK_HEIGHT = 46;
export const SPEED_HEIGHT = 30;

// A run is `{start, length, buttons, stick_x, stick_y, yaw, speed}` on the
// capture axis (zero-based, sorted), with holes between runs where capture
// stopped. The field names are the payload's own, so a field added on the
// server is readable here the moment it arrives.
export function frameAt(runs, frame) {
  if (frame == null) return null;
  // A frame inside a hole has no reading and answers null -- never the
  // neighbouring run's, which would interpolate across exactly the gap the
  // format exists to preserve.
  let low = 0;
  let high = runs.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    const run = runs[mid];
    if (frame < run.start) high = mid - 1;
    else if (frame >= run.start + run.length) low = mid + 1;
    else return run;
  }
  return null;
}

export function actionAt(spans, frame) {
  if (frame == null) return null;
  for (const span of spans || []) {
    if (frame >= span.start && frame < span.start + span.length) return span;
  }
  return null;
}

// The last moment at or before `frame`: what he had most recently done to
// the world when the playhead sits here. Markers are sorted by frame.
export function momentAt(markers, frame) {
  if (frame == null) return null;
  let found = null;
  for (const marker of markers || []) {
    if (marker.frame > frame) break;
    found = marker;
  }
  return found;
}

// The buttons SM64 play is made of. These lanes ALWAYS draw, pressed or
// not, so the rows sit in the same place on every attempt and an unused C
// button is visibly empty rather than absent (his report, 2026-08-22: "our
// list of inputs is missing C-up for some reason. C-Left, C-Right, C-Down,
// C-Up should be captured clearly"). Start, the D-pad, L and R appear only
// when pressed -- fourteen lanes would bury the seven that carry a run.
export const CORE_BUTTONS = ["A", "B", "Z", "Cup", "Cdown", "Cleft", "Cright"];

export function lanesOf(runs, table) {
  return (table || []).map(([bit, name]) => {
    const bars = [];
    for (const run of runs) {
      if (!(run.buttons & bit)) continue;
      const last = bars[bars.length - 1];
      if (last && last.start + last.length === run.start) last.length += run.length;
      else bars.push({ start: run.start, length: run.length });
    }
    return { bit, name, bars };
  }).filter((lane) => lane.bars.length > 0 || CORE_BUTTONS.includes(lane.name));
}

// A step line: one value held across each run, drawn as a horizontal segment
// from the run's start to its end. `valueOf` picks the field; `scale` maps it
// into the lane's height.
function stepPath(runs, valueOf, scale) {
  const points = [];
  for (const run of runs) {
    const y = scale(valueOf(run)).toFixed(2);
    points.push(`${run.start},${y}`, `${run.start + run.length},${y}`);
  }
  return points.join(" ");
}

// Each capture hole breaks the drawing. A polyline joining either side of a
// hole invents a stick movement or speed where neither was recorded.
export function contiguousRuns(runs) {
  const groups = [];
  for (const run of runs) {
    const group = groups[groups.length - 1];
    const previous = group && group[group.length - 1];
    if (previous && previous.start + previous.length === run.start) group.push(run);
    else groups.push([run]);
  }
  return groups;
}

export function curvePath(runs, pointsOf) {
  return contiguousRuns(runs).map((group) =>
    `M ${pointsOf(group).split(" ").join(" L ")}`).join(" ");
}

// Speed is drawn against the fastest value in THIS track, not a fixed cap:
// what he asked for is "where there are opportunities to go faster", which is
// a comparison within one run. A fixed ceiling would flatten a whole slow
// segment into a line at the bottom and hide exactly that.
//
// With a template behind it, BOTH curves share the faster track's peak: two
// curves on two scales would put the slower run's top at the same height as
// the faster run's, which is the opposite of the comparison he asked for.
export function speedPeak(...tracks) {
  let peak = 0;
  for (const runs of tracks) {
    for (const run of runs || []) peak = Math.max(peak, Math.abs(run.speed));
  }
  return peak > 0 ? peak : 1;
}

export function speedPath(runs, peak) {
  return stepPath(runs, (run) => Math.abs(run.speed),
    (speed) => SPEED_HEIGHT - (speed / peak) * (SPEED_HEIGHT - 2));
}

// The stick's reach is the pad's own, not the game's cap: his pad reaches 84
// where the game clamps at 64, so scaling to the cap would pin every full
// deflection to the lane's edge (the same call controllerpanel.js makes).
export function stickReach(stickMax, ...tracks) {
  let reach = stickMax;
  for (const runs of tracks) {
    for (const run of runs) reach = Math.max(reach, Math.abs(run.stick_x), Math.abs(run.stick_y));
  }
  return reach;
}

export function stickPath(runs, axis, reach) {
  const valueOf = (run) => (axis === "x" ? run.stick_x : run.stick_y);
  return stepPath(runs, valueOf,
    (value) => STICK_HEIGHT / 2 - (value / reach) * (STICK_HEIGHT / 2 - 2));
}

// Times read as SECONDS, not as a frame count. His round-32 ask, quoting
// k8ehops: "instead of using 30f or 35f, use xx.xx format instead, since thats
// what most people read times as". `fmtIgtShort` is the project's own display
// form, so this surface cannot spell a time differently from the rest.
export const timeLabel = fmtIgtShort;

export function spanLabel(start, length, lead = 0) {
  start -= lead;
  return length === 1
    ? timeLabel(start)
    : `${timeLabel(start)}–${timeLabel(start + length - 1)}`;
}

// THE clock mapping, in both directions. `anchorOffsetS` is how far into
// the clip the attempt's anchor sits: the clip is cut a few seconds BEFORE
// the anchor (the replay pre-pad) while the track starts AT it. Without it
// every input landed three seconds early (his first live run, 2026-08-22:
// "the input reader shows a totally different angle and shows me pressing
// A/B"). Pure and exported so tests/test_ui_input_clock.py can drive them.
//
// This arithmetic is the FALLBACK. A clip whose sidecar carries a
// `frame_map` uses the mapped pair below instead: the capture duplicates
// and skips single game frames (round 32 items 16/24 -- his counter read
// 26, 27, 27, 29, ...), so no offset can be right on every frame, and his
// ruling was "we need 100% accuracy". The map says, per video frame, which
// game frame its picture shows; the arithmetic remains for clips cut
// before the frame clock existed.
export const frameAtTime = (seconds, anchorOffsetS, fps, frames) => {
  const raw = Math.floor((seconds - anchorOffsetS) * fps + 1e-4);
  return Math.max(0, Math.min(Math.max(frames - 1, 0), raw));
};
export const timeAtFrame = (frame, anchorOffsetS, fps) =>
  anchorOffsetS + (frame + 0.5) / fps;

// A raw game frame <-> the track's zero-based axis, through the payload's
// `stretches` ([axis_start, raw_start, length] per ascending stretch of the
// counter -- the server's own restart rule, shipped rather than re-derived).
export function trackFrameOf(raw, stretches) {
  let found = null;
  for (const [axisStart, rawStart, length] of stretches || []) {
    if (raw >= rawStart && raw < rawStart + length) {
      // A raw counter is not an identity across save-state resets. Until
      // a capture occurrence resolves the choice, neither visit is known.
      if (found !== null) return null;
      found = axisStart + (raw - rawStart);
    }
  }
  return found;
}
export function gameFrameOf(axis, stretches) {
  for (const [axisStart, rawStart, length] of stretches || []) {
    if (axis >= axisStart && axis < axisStart + length) {
      return rawStart + (axis - axisStart);
    }
  }
  return null;
}

// The mapped clock. `frameMap[k]` is the raw game frame video frame k shows
// (null before the clock's coverage); `clock` is the clip clock
// (frame.js::clipClock -- the encode rate and first timestamp of a CFR
// clip, or every frame's own time for a picture-feed clip). Answers null
// when the map cannot identify an input. The panel must preserve that
// absence rather than borrow a nearby input from a time estimate.
export function mappedFrameAtTime(seconds, frameMap, clock, stretches, frames) {
  if (!frameMap || !frameMap.length) return null;
  const slot = slotAtTime(seconds, clock);
  if (slot < 0 || slot >= frameMap.length) return null;
  const raw = frameMap[slot];
  if (raw == null) return null;
  const axis = trackFrameOf(raw, stretches);
  return axis !== null && axis >= 0 && axis < frames ? axis : null;
}
export function mappedOccurrence(raw, frameMap) {
  // Prefer the exact counter within one visit. A preceding epoch can have
  // larger counters, so >= alone would seek there before finding this frame.
  let first = null, matchedEpoch = null, epoch = 0, previous = null;
  for (let slot = 0; slot < frameMap.length; slot += 1) {
    const shown = frameMap[slot];
    if (shown == null) continue;
    if (previous != null && shown < previous) epoch += 1;
    if (shown === raw) {
      if (matchedEpoch !== null && matchedEpoch !== epoch) return null;
      if (first === null) first = slot;
      matchedEpoch = epoch;
    }
    previous = shown;
  }
  return { first, epoch };
}

export function mappedTimeAtFrame(frame, frameMap, clock, stretches) {
  if (frame == null || !frameMap || !frameMap.length) return null;
  const raw = gameFrameOf(frame, stretches);
  if (raw === null || trackFrameOf(raw, stretches) !== frame) return null;
  const occurrence = mappedOccurrence(raw, frameMap);
  if (!occurrence) return null;
  const { first, epoch } = occurrence;
  if (first !== null) return timeOfSlot(first, clock);
  // A skipped frame can choose the next capture only on an ascending map.
  // Without an occurrence match, a reset makes that neighbor ambiguous.
  if (epoch) return null;
  for (let slot = 0; slot < frameMap.length; slot += 1) {
    const shown = frameMap[slot];
    if (shown != null && shown >= raw) return timeOfSlot(slot, clock);
  }
  return null;
}

export function mappedLoopWindow(loop, frameMap, clock, stretches, total) {
  if (!loop || !(loop.end > loop.start)) return null;
  let lastSlot = slotAtTime(loop.end, clock);
  // B is the exclusive edge of the selected picture. Using the next
  // picture would add an input the player did not include in the loop.
  if (timeOfSlot(lastSlot, clock) >= loop.end) lastSlot -= 1;
  if (lastSlot < 0) return null;
  const start = mappedFrameAtTime(loop.start, frameMap, clock, stretches, total);
  const last = mappedFrameAtTime(timeOfSlot(lastSlot, clock), frameMap, clock, stretches, total);
  if (start == null || last == null || last < start
      || mappedTimeAtFrame(start, frameMap, clock, stretches) == null
      || mappedTimeAtFrame(last, frameMap, clock, stretches) == null) return null;
  return { start, end: Math.min(total, last + 1) };
}

export function loopFromFrames(a, b, frameMap, clock, stretches, total) {
  const first = Math.min(a, b), last = Math.max(a, b);
  const startTime = mappedTimeAtFrame(first, frameMap, clock, stretches);
  const endTime = mappedTimeAtFrame(last, frameMap, clock, stretches);
  if (startTime == null || endTime == null || endTime < startTime
      || mappedFrameAtTime(startTime, frameMap, clock, stretches, total) !== first
      || mappedFrameAtTime(endTime, frameMap, clock, stretches, total) !== last) return null;
  const start = pictureInterval(startTime, clock, null, clock.duration)?.start;
  let slot = slotAtTime(endTime, clock);
  while (slot + 1 < frameMap.length && mappedFrameAtTime(timeOfSlot(slot + 1, clock),
    frameMap, clock, stretches, total) === last) slot += 1;
  const end = pictureInterval(timeOfSlot(slot, clock), clock, null, clock.duration)?.end;
  return Number.isFinite(start) && end > start ? { start, end, enabled: true } : null;
}

// THE INSPECTOR'S CLOCK. A capture-layer clip stamps every picture with the
// IGT the game held when it drew that picture -- the number Usamune printed
// on screen -- so the inspector shows that for the slot on screen, and only
// counts from the track's first frame (`frame - lead`) when no stamp is
// there. The two clocks start a frame or two apart (the track is cut to the
// attempt's final time), which is what he saw as "the time is always one
// frame later" on his first plugin clip (2026-09-05). Returns {frames,
// stamped}, or null in the lead-in with nothing stamped.
export function inspectorClock(frame, lead, pictureIgt, slot) {
  if (pictureIgt && slot != null && slot >= 0 && slot < pictureIgt.length) {
    const igt = pictureIgt[slot];
    if (igt != null) return { frames: igt, stamped: true };
  }
  if (frame == null || frame < lead) return null;
  return { frames: frame - lead, stamped: false };
}

import { h } from "preact";
import htm from "htm";
import { ControllerPanel, FacingDial, stickPhrase } from "./controllerpanel.js";
import { frameAt, actionAt, momentAt, inspectorClock, timeLabel } from "./inputtimelinemodel.js";

const html = htm.bind(h);

function InspectorFrame({ frame, lead, total, video, pictureIgt, presentedSlot }) {
  return html`
      <div class="input-inspector-frame">
        <span class="eyebrow">Frame</span>
        ${/* BOTH HALVES ARE FRAME NUMBERS -- never a number over a count.
              The axis is zero-based (frame 0 is the attempt's own start, the
              lead-in counts backwards from it), so a 499-frame track's last
              frame IS 498, and printing the COUNT beside it meant the
              readout could never reach its own denominator. His report,
              2026-09-05: "we always stop before the last frame of the
              video... from a user perspective this looks like an error, not
              intentional." The last frame now names itself: 498 / 498. */""}
        <strong>${frame == null ? "—" : frame - lead} / ${Math.max(0, total - lead - 1)}</strong>
        ${(() => {
          // Keep the presented slot. Reversing through the raw counter can
          // select a previous visit and show that visit's IGT after a reset.
          const shown = inspectorClock(video ? null : frame, lead, pictureIgt, presentedSlot);
          if (!shown) return html`<span class="meta">${frame != null && frame < lead ? "lead-in" : "Time unavailable"}</span>`;
          return html`<span class="meta ${shown.stamped ? "is-stamped" : ""}"
              title=${shown.stamped ? "the game's own timer in this picture"
                                    : "counted from the attempt's first frame"}>
            ${timeLabel(shown.frames)}</span>`;
        })()}
      </div>
  `;
}

function InspectorRead({ data, frame, here, nowDoing, thereDoing, lastMoment, lead }) {
  return html`
      <div class="input-inspector-read">
        ${here
          ? html`<span>Stick ${stickPhrase(here.stick_x, here.stick_y,
              data.dead_zone, data.stick_max)}</span>`
          : html`<span class="is-error">${frame == null ? "Input unavailable for this picture" : "No capture on this frame"}</span>`}
        ${nowDoing && html`<span class="input-inspector-action">
          ${nowDoing.label}</span>`}
        ${thereDoing && html`<span class="input-inspector-action is-template"
            title="What the template was doing on this frame">
          template: ${thereDoing.label}</span>`}
        ${lastMoment && html`<span class="input-inspector-moment"
            title="The last moment before this frame">
          ${lastMoment.label}${" "}<span class="meta">at ${timeLabel(lastMoment.frame - lead)}</span></span>`}
      </div>
  `;
}

function inspectorInput(data, frame, video, pictureStates, presentedSlot) {
  // The poller can miss a late pad change in this game frame. Read the state
  // retained with the actual presented picture, in O(1), even across repeated
  // raw counters. An unknown slot is not permission to borrow a track sample.
  if (video && Array.isArray(pictureStates)) {
    return Number.isInteger(presentedSlot) ? pictureStates[presentedSlot] ?? null : null;
  }
  return frameAt(data.runs, frame);
}

function inspectorAction(data, frame, video, pictureStates, here) {
  const action = actionAt(data.actions, frame);
  return video && Array.isArray(pictureStates) && action?.action !== here?.action ? null : action;
}

export function TimelineInspector({ data, template, frame, lead, total, video, pictureIgt,
                                    pictureStates, presentedSlot }) {
  const here = inspectorInput(data, frame, video, pictureStates, presentedSlot);
  const nowDoing = inspectorAction(data, frame, video, pictureStates, here);
  const markers = data.markers || [];
  const lastMoment = momentAt(markers, frame);
  const there = template ? frameAt(template.runs, frame) : null;
  const thereDoing = template ? actionAt(template.actions, frame) : null;
  return html`<footer class="input-inspector">
    <${InspectorFrame} frame=${frame} lead=${lead} total=${total} video=${video}
      pictureIgt=${pictureIgt} presentedSlot=${presentedSlot} />
      <${ControllerPanel} frame=${here} buttons=${data.buttons}
          stickMax=${data.stick_max} deadZone=${data.dead_zone}
          label=${template ? "Stick" : "Pressing"}
          templateFrame=${template ? there : undefined} />
      <${FacingDial} yaw=${here ? here.yaw : null}
          angleUnits=${data.angle_units} speed=${here?.speed ?? null}
          templateYaw=${template ? (there ? there.yaw : null) : undefined}
          templateSpeed=${there ? there.speed : null}
          label="Mario faces" />
    <${InspectorRead} data=${data} frame=${frame} here=${here} nowDoing=${nowDoing}
      thereDoing=${thereDoing} lastMoment=${lastMoment} lead=${lead} />
  </footer>`;
}

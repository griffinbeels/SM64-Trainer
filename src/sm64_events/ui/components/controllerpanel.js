// src/sm64_events/ui/components/controllerpanel.js
// The pad as Usamune draws it: the stick box, the two raw numbers, and a pip
// per button held. Usamune calls the three modes ONVIS (the box), ONVAL (the
// numbers) and ONCOMB (both); this is ONCOMB, because "how much up-right am I
// holding on this frame" has to be readable as a NUMBER, not deduced from a
// dot's position.
//
// TWO CONSUMERS, one drawing: the timeline's frame inspector and the
// transparent overlay export. The export inherits every refinement made here
// rather than growing a second controller of its own.
//
// It never names a button bit. The table arrives from the server
// (`/api/attempts/{id}/inputs` sends `buttons`), which is what makes the
// duplicate impossible to write rather than merely discouraged.
import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

const BOX = 100;          // the stick box's own coordinate space
const DOT = 9;

// Which way the raw stick is leaning, in the words Usamune's own display
// uses -- and to the same digit. Only an axis at EXACTLY zero reads as
// nothing: a reading inside the game's dead zone is still a reading, and
// Usamune prints it (his report 2026-08-23: "U19 L2 in game, but U19 with a
// -- entry in the input display... It should match Usamune identically").
// The dead zone is a fact about what the GAME does with the value, which
// `stickPhrase` says in words; it is not a reason to hide the value.
export function stickWords(stickX, stickY) {
  const vertical = stickY === 0
    ? null : `${stickY > 0 ? "U" : "D"}${Math.abs(stickY)}`;
  const horizontal = stickX === 0
    ? null : `${stickX > 0 ? "R" : "L"}${Math.abs(stickX)}`;
  return { vertical, horizontal };
}

export function heldNames(buttons, table) {
  return (table || []).filter(([bit]) => (buttons & bit) !== 0)
    .map(([, name]) => name);
}

// A stick reading as a person would say it: direction plus how far.
export function stickPhrase(stickX, stickY, deadZone = 8, stickMax = 64) {
  const magnitude = Math.hypot(stickX, stickY);
  if (magnitude < deadZone) return "neutral";
  const octants = ["R", "UR", "U", "UL", "L", "DL", "D", "DR"];
  const degrees = (Math.atan2(stickY, stickX) * 180 / Math.PI + 360) % 360;
  const octant = octants[Math.floor((degrees + 22.5) / 45) % 8];
  const band = magnitude >= stickMax ? "full"
    : (magnitude >= stickMax / 2 ? "half" : "light");
  return `${octant} ${band}`;
}

// Which way MARIO is facing, as a compass dial. Deliberately the same shape
// as the stick box beside it, because his own words were "the controller
// input direction display could maybe be reused as a way to display mario's
// actual direction (these two different things)" -- one visual language, two
// facts: what you are PUSHING, and where he is POINTING.
//
// It is a DIAL rather than a box: a facing is an angle with no magnitude, so a
// square would imply a reach that does not exist.
//
// `yaw: null` is a frame with NO capture -- the dial draws its ring and no
// needle, and reads "--", because 0 degrees is a real bearing and a frame
// nobody recorded must not claim it.
export function FacingDial({ yaw, angleUnits = 0x10000, size = 108,
                             speed = null, label = null }) {
  const known = yaw !== null && yaw !== undefined;
  const degrees = known
    ? ((yaw % angleUnits) + angleUnits) % angleUnits * 360 / angleUnits : 0;
  // Screen space: 0 units is +x (east) and the angle grows anticlockwise in
  // the game's own convention, which is what the stick box already draws.
  const radians = degrees * Math.PI / 180;
  const radius = BOX / 2 - 10;
  const tipX = BOX / 2 + Math.cos(radians) * radius;
  const tipY = BOX / 2 - Math.sin(radians) * radius;
  const compass = Math.round(degrees);

  return html`<div class="controller-panel facing-panel"
                   style=${`--panel-size:${size}px`}>
    ${label && html`<span class="controller-panel-label">${label}</span>`}
    <div class="controller-panel-body">
      <svg class="stick-box facing-dial" viewBox=${`0 0 ${BOX} ${BOX}`}
           width=${size} height=${size}
           aria-label=${known ? `Facing ${compass} degrees` : "Facing unknown"}>
        <circle cx=${BOX / 2} cy=${BOX / 2} r=${BOX / 2 - 3}
                class="facing-ring" />
        ${[0, 90, 180, 270].map((tick) => {
          const at = tick * Math.PI / 180;
          return html`<line key=${tick}
            x1=${BOX / 2 + Math.cos(at) * (BOX / 2 - 8)}
            y1=${BOX / 2 - Math.sin(at) * (BOX / 2 - 8)}
            x2=${BOX / 2 + Math.cos(at) * (BOX / 2 - 3)}
            y2=${BOX / 2 - Math.sin(at) * (BOX / 2 - 3)}
            class="facing-tick" />`;
        })}
        ${known && html`
          <line x1=${BOX / 2} y1=${BOX / 2} x2=${tipX} y2=${tipY}
                class="facing-needle" />
          <circle cx=${tipX} cy=${tipY} r=${DOT / 2} class="facing-head" />`}
        <circle cx=${BOX / 2} cy=${BOX / 2} r="2.5" class="facing-hub" />
      </svg>
      <div class="stick-values">
        <span class=${`stick-value ${known ? "" : "is-centred"}`}>
          ${known ? `${compass}°` : "--"}</span>
        ${speed !== null && html`<span class=${`stick-value is-speed ${known ? "" : "is-centred"}`}
            title="Mario's forward speed on this frame">
          ${known ? `${Math.round(speed * 10) / 10} spd` : "--"}</span>`}
      </div>
    </div>
  </div>`;
}

export function ControllerPanel({
  frame, buttons: table, stickMax = 64, deadZone = 8, size = 108,
  showNumbers = true, showButtons = true, label = null,
}) {
  // `frame` is a timeline run (`{buttons, stick_x, stick_y, ...}`) or null
  // for a frame with no capture, which draws as a centred, empty pad.
  const stickX = frame ? frame.stick_x : 0;
  const stickY = frame ? frame.stick_y : 0;
  const held = frame ? heldNames(frame.buttons, table) : [];
  const { vertical, horizontal } = stickWords(stickX, stickY);
  // The box shows the stick's reach, and the pad reaches past the game's own
  // cap of 64 -- his own maximum is 84 -- so clamping to the cap here would
  // park the dot on the edge for every full deflection and lose the
  // difference between "at the cap" and "pushed past it".
  const reach = Math.max(stickMax, Math.abs(stickX), Math.abs(stickY), 1);
  const toBox = (value) => BOX / 2 + (value / reach) * (BOX / 2 - DOT / 2);

  return html`<div class="controller-panel" style=${`--panel-size:${size}px`}>
    ${label && html`<span class="controller-panel-label">${label}</span>`}
    <div class="controller-panel-body">
      <svg class="stick-box" viewBox=${`0 0 ${BOX} ${BOX}`}
           width=${size} height=${size} aria-hidden="true">
        <rect x="2" y="2" width=${BOX - 4} height=${BOX - 4} rx="6"
              class="stick-box-frame" />
        <line x1=${BOX / 2} y1="8" x2=${BOX / 2} y2=${BOX - 8}
              class="stick-box-cross" />
        <line x1="8" y1=${BOX / 2} x2=${BOX - 8} y2=${BOX / 2}
              class="stick-box-cross" />
        <circle cx=${BOX / 2} cy=${BOX / 2} r=${(BOX / 2 - DOT / 2) * (stickMax / reach)}
                class="stick-box-cap" />
        <line x1=${BOX / 2} y1=${BOX / 2} x2=${toBox(stickX)}
              y2=${BOX - toBox(stickY)} class="stick-box-stem" />
        <circle cx=${toBox(stickX)} cy=${BOX - toBox(stickY)} r=${DOT / 2}
                class="stick-box-dot" />
      </svg>
      ${showNumbers && html`<div class="stick-values"
          aria-label=${`Stick ${stickPhrase(stickX, stickY, deadZone, stickMax)}`}>
        <span class=${`stick-value ${vertical ? "" : "is-centred"}`}>
          ${vertical || "--"}</span>
        <span class=${`stick-value ${horizontal ? "" : "is-centred"}`}>
          ${horizontal || "--"}</span>
      </div>`}
    </div>
    ${showButtons && html`<div class="controller-buttons"
        aria-label=${held.length ? `Holding ${held.join(", ")}` : "No buttons held"}>
      ${held.length === 0
        ? html`<span class="controller-button is-empty">no buttons</span>`
        : held.map((name) => html`
          <span class=${`controller-button btn-${name.toLowerCase()}`}
                key=${name}>${name}</span>`)}
    </div>`}
  </div>`;
}

// src/sm64_events/ui/scenecameratuning.js -- THE registry of every number that
// decides how the Scene viewer's free camera FEELS: turning around the focus
// point, sliding, moving closer, flying, and how fast a rolled view levels.
//
// The design asks for "the best free cam controller ever" and says plainly that
// prior art does not prove what is easiest for this audience, so the feel is
// Griffin's to judge (.claude/skills/tuning-demo): the Tune controls panel inside
// the Scene viewer draws one control per row, applies a change on the next
// movement, and SAVE (POST /api/tuning/scenecamera) writes the numbers back
// into the `value:` fields below, which ARE the shipped defaults.
//
// Same contract as climbtuning.js / feedtuning.js. components/scenecamera.js
// is the one module that reads these rows (each function takes the tuning as
// an argument, defaulting to the shipped values); sceneviewer.js reads the
// active slot once per movement and passes it in. Import-free so node can run
// it directly (tests/test_ui_scenecameratuning.py).
//
// A row: group / label / value (THE SHIPPED DEFAULT) / min / max / step /
// unit / why (one line, the control's tooltip). Ranges are wider than
// sensible on purpose: a value has to be pushed past good to find where good was.

export const SCENE_CAMERA_TUNABLES = {
  // --- turning around the focus point (left drag) --------------------------
  orbitPerPixel: {
    group: "Orbit", label: "Turn per pixel dragged", value: 0.006,
    min: 0.0005, max: 0.03, step: 0.0005, unit: "rad",
    why: "How far a left drag turns the camera around the focus point. Too low and a half turn takes two swipes; too high and a small correction overshoots.",
  },
  steepestDegrees: {
    group: "Orbit", label: "Steepest look up or down", value: 83,
    min: 45, max: 89.5, step: 0.5, unit: "deg",
    why: "How close to straight down (or up) turning may go. Past about 89 the horizon spins around the focus point. A recorded camera steeper than this keeps its angle on entry; turning only brings it back toward the range.",
  },
  levelPerPixel: {
    group: "Orbit", label: "Level a tilted view per pixel", value: 0.05,
    min: 0, max: 2, step: 0.01, unit: "deg",
    why: "A recorded camera can be tilted (a shake rolls the screen). Free camera starts tilted, exactly as recorded, and turning levels it by this much per pixel. Zero keeps the recorded tilt until Reset view or Look at Mario.",
  },

  // --- sliding and moving closer (Shift-drag, wheel) -----------------------
  panPerPixel: {
    group: "Pan & dolly", label: "Slide per pixel", value: 0.00375,
    min: 0.0002, max: 0.02, step: 0.00005, unit: "of distance",
    why: "Shift-drag (or a middle-button drag) slides the scene by this fraction of the distance to the focus point per pixel, so a close look at Mario moves in small steps and a stage-wide view in large ones. Raised from 0.0015 to 0.00375 (2.5x) on Griffin's first try: 'I fly further per drag'.",
  },
  dollyPerWheel: {
    group: "Pan & dolly", label: "Move closer per wheel unit", value: 0.001,
    min: 0.0001, max: 0.005, step: 0.0001, unit: "of distance",
    why: "The wheel scales the distance to the focus point by e^(this x wheel delta): one notch (100) at 0.001 moves about 10%, so it is as precise near a wall as it is quick across the stage.",
  },
  closestDistance: {
    group: "Pan & dolly", label: "Closest to the focus point", value: 50,
    min: 5, max: 500, step: 5, unit: "units",
    why: "The wheel stops this far from the focus point. Mario is about 160 units tall. A recorded camera closer than this keeps its place on entry.",
  },
  farthestDistance: {
    group: "Pan & dolly", label: "Farthest from the focus point", value: 30000,
    min: 2000, max: 60000, step: 500, unit: "units",
    why: "The wheel stops this far out. Bob-omb Battlefield is about 16000 units across.",
  },

  // --- flying (hold the right button, WASD, Q/E) ---------------------------
  flightSpeed: {
    group: "Flight", label: "Flight speed at 1x", value: 1500,
    min: 100, max: 10000, step: 50, unit: "units/s",
    why: "How fast W A S D and Q E move the camera at the speed control's 1x. Mario runs at about 960 units a second. No acceleration: it moves at this speed from the first frame a key is down and stops the frame it is let go.",
  },
  lookPerPixel: {
    group: "Flight", label: "Look per pixel while flying", value: 0.003,
    min: 0.0005, max: 0.02, step: 0.0005, unit: "rad",
    why: "How far moving the mouse with the right button held turns the view. The camera turns where it stands, not around the focus point.",
  },
  fastMultiplier: {
    group: "Flight", label: "Shift: faster by", value: 4,
    min: 1, max: 20, step: 0.5, unit: "x",
    why: "Holding Shift while flying multiplies the speed by this.",
  },
  slowMultiplier: {
    group: "Flight", label: "Alt: slower by", value: 0.25,
    min: 0.02, max: 1, step: 0.01, unit: "x",
    why: "Holding Alt while flying multiplies the speed by this, for lining up on a ledge.",
  },
  speedPerNotch: {
    group: "Flight", label: "Wheel changes speed by", value: 1.25,
    min: 1.02, max: 3, step: 0.01, unit: "x",
    why: "While flying, each wheel notch multiplies (up) or divides (down) the flight speed by this; the speed control's - and + buttons step by the same factor.",
  },
  levelPerSecond: {
    group: "Flight", label: "Level a tilted view per second", value: 20,
    min: 0, max: 180, step: 1, unit: "deg",
    why: "While flying, a recorded tilt levels by this much each second.",
  },

  // --- double-click: fly to the clicked surface, then look at Mario ----------
  flyToStandoff: {
    group: "Fly to (double-click)", label: "Stop short of the surface by", value: 250,
    min: 20, max: 3000, step: 10, unit: "units",
    why: "Double-click flies to the clicked surface and stops this far in front of it, back along the click's line toward where the camera was, so it sits in front of a wall, never inside it. Mario is about 160 units tall.",
  },
  flyToMs: {
    group: "Fly to (double-click)", label: "Flight takes", value: 320,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "How long the double-click flight lasts. It eases out of the old view and into the new one; any input stops it where it is. Zero jumps straight there.",
  },

  // --- Look at Mario (F) ------------------------------------------------------
  focusDistance: {
    group: "Look at Mario", label: "Distance it brings you to", value: 700,
    min: 100, max: 5000, step: 50, unit: "units",
    why: "Look at Mario keeps the camera's direction and comes this close when it was farther; nearer than this it stays where it is.",
  },
  marioHeight: {
    group: "Look at Mario", label: "Focus height above his feet", value: 120,
    min: 0, max: 300, step: 5, unit: "units",
    why: "Where on Mario the focus point sits: 0 is his feet, about 160 the top of his cap.",
  },
};

export const SCENE_CAMERA_DEFAULTS = Object.freeze(Object.fromEntries(
  Object.entries(SCENE_CAMERA_TUNABLES).map(([key, row]) => [key, row.value])));

export const SCENE_CAMERA_GROUPS = [...new Set(
  Object.values(SCENE_CAMERA_TUNABLES).map((row) => row.group))];

// A tunable a caller did not name falls back to the shipped default rather
// than to undefined (a NaN distance would send the camera nowhere); unknown
// keys are dropped, so a draft kept from before a row was renamed cannot make
// SAVE fail on a name he has never seen.
export const withSceneCameraDefaults = (values) =>
  ({ ...SCENE_CAMERA_DEFAULTS,
     ...Object.fromEntries(Object.entries(values || {})
       .filter(([key, value]) => key in SCENE_CAMERA_DEFAULTS && Number.isFinite(value))) });

let active = withSceneCameraDefaults(null);
export const sceneCameraTuning = () => active;
export function setSceneCameraTuning(values) {
  active = withSceneCameraDefaults(values);
  return active;
}

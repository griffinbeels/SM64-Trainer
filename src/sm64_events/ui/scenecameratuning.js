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

  // --- flying (W A S D, Q/E; the right button adds looking around) --------
  flightSpeed: {
    group: "Flight", label: "Flight speed at 1x", value: 1500,
    min: 100, max: 10000, step: 50, unit: "units/s",
    why: "How fast W A S D and Q E move the camera at the speed control's 1x, with or without the right button held. Mario runs at about 960 units a second. No acceleration: it moves at this speed from the first frame a key is down and stops the frame it is let go.",
  },
  lookPerPixel: {
    group: "Flight", label: "Look per pixel while flying", value: 0.003,
    min: 0.0005, max: 0.02, step: 0.0005, unit: "rad",
    why: "How far moving the mouse with the right button held turns the view. The camera turns where it stands, not around the focus point.",
  },
  fastMultiplier: {
    group: "Flight", label: "Shift: faster by", value: 4,
    min: 1, max: 20, step: 0.5, unit: "x",
    why: "Holding Shift with W A S D or Q E multiplies the speed by this.",
  },
  slowMultiplier: {
    group: "Flight", label: "Alt: slower by", value: 0.25,
    min: 0.02, max: 1, step: 0.01, unit: "x",
    why: "Holding Alt with W A S D or Q E multiplies the speed by this, for lining up on a ledge.",
  },
  speedPerNotch: {
    group: "Flight", label: "Wheel changes speed by", value: 1.25,
    min: 1.02, max: 3, step: 0.01, unit: "x",
    why: "While the right button is held, each wheel notch multiplies (up) or divides (down) the flight speed by this; the speed control's - and + buttons step by the same factor.",
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
    group: "Fly to (double-click)", label: "Flight takes (ms)", value: 320,
    min: 0, max: 1500, step: 10, unit: "ms",
    why: "How long the double-click flight lasts. It eases out of the old view and into the new one; any input stops it where it is. Zero jumps straight there.",
  },

  // --- Look at Mario (F) ------------------------------------------------------
  marioHeight: {
    group: "Look at Mario", label: "Focus height above his feet", value: 120,
    min: 0, max: 300, step: 5, unit: "units",
    why: "Where on Mario the focus point sits: 0 is his feet, about 160 the top of his cap. The cinematic cameras aim here too, and stand at this height until you raise or lower them.",
  },

  // --- Cinematic cameras (Front, Side, Orbit) ---------------------------------
  // The server reads these rows for a request that leaves them out
  // (server/tuning_api.py shipped_numbers; tools/scene_lab/camera_rig.py).
  rigCloseDistance: {
    group: "Cinematic", label: "Close shot: distance from him", value: 220,
    min: 60, max: 1500, step: 10, unit: "units",
    why: "How far the Front and Side cameras stand from Mario for a close shot. Mario is about 160 units tall.",
  },
  rigMediumDistance: {
    group: "Cinematic", label: "Medium shot: distance from him", value: 500,
    min: 150, max: 4000, step: 10, unit: "units",
    why: "How far the Front and Side cameras stand from Mario for a medium shot.",
  },
  rigWideDistance: {
    group: "Cinematic", label: "Wide shot: distance from him", value: 1200,
    min: 300, max: 10000, step: 50, unit: "units",
    why: "How far the Front and Side cameras stand from Mario for a wide shot.",
  },
  rigFov: {
    group: "Cinematic", label: "Field of view", value: 45,
    min: 15, max: 100, step: 1, unit: "deg",
    why: "How wide the cinematic cameras see. The game draws most of a course at 45.",
  },
  rigSmoothFrames: {
    group: "Cinematic", label: "Smooth his path over", value: 12,
    min: 0, max: 60, step: 1, unit: "frames each side",
    why: "The camera rides a smoothed copy of his path, looking this many frames ahead and behind, so it glides instead of copying every hop and wobble. More is smoother but cuts corners wider; 0 follows him exactly.",
  },
  rigAimFrames: {
    group: "Cinematic", label: "Smooth the aim over", value: 2,
    min: 0, max: 30, step: 1, unit: "frames each side",
    why: "The camera aims at him, smoothed over this many frames each side. 0 keeps him dead center; more lets him drift in the frame on a jump.",
  },
  rigStillSpeed: {
    group: "Cinematic", label: "Below this speed, front is where he faces", value: 200,
    min: 0, max: 2000, step: 10, unit: "units/s",
    why: "Front is the way he travels. Slower than this (standing, turning on the spot) it eases over to the way he faces, so the Front camera stays in front of his face. Mario runs at about 960 units a second.",
  },
  rigSideLead: {
    group: "Cinematic", label: "Side camera sits ahead of him by", value: 0,
    min: -2000, max: 2000, step: 10, unit: "units",
    why: "Moves the Side camera along his path: ahead of him he runs into the shot, behind him he runs away from it. 0 is a straight profile.",
  },
  rigSideHoldFrames: {
    group: "Cinematic", label: "Switch sides for a wall lasting", value: 20,
    min: 1, max: 300, step: 1, unit: "frames",
    why: "The Side camera keeps its side until a wall would block it this many frames in a row and the other side is clear; a shorter block only pulls it in. Lower switches sooner, higher cuts across less often.",
  },
  rigSideStayFrames: {
    group: "Cinematic", label: "Stay on a side at least", value: 45,
    min: 0, max: 300, step: 1, unit: "frames",
    why: "After the Side camera cuts across, it stays on the new side at least this long (45 frames is 1.5 seconds), so walls close together never make it flick back and forth; meanwhile a wall only lifts or pulls it in.",
  },
  rigWallStandoff: {
    group: "Cinematic", label: "Stop short of a wall by", value: 60,
    min: 0, max: 500, step: 5, unit: "units",
    why: "When a wall stands between Mario and a cinematic camera, the camera moves in along that line and stops this far in front of the wall.",
  },
  rigCraneDistance: {
    group: "Cinematic", label: "Rise over a wall closer than", value: 250,
    min: 0, max: 3000, step: 10, unit: "units",
    why: "When a wall would leave a cinematic camera nearer Mario than this, the camera rises over it (looking down at him) before it moves in. The game draws nothing within 100 units of its camera, so a camera pulled in that close loses him. 0 never rises.",
  },
  rigPullFrames: {
    group: "Cinematic", label: "Ease a wall's rise and pull over", value: 8,
    min: 0, max: 60, step: 1, unit: "frames each side",
    why: "A camera a wall makes rise or move in starts this many frames before the wall reaches it and eases back as long after, instead of jumping. 0 jumps.",
  },
  rigOrbitRadius: {
    group: "Cinematic", label: "Orbit: radius to start with", value: 700,
    min: 50, max: 20000, step: 10, unit: "units",
    why: "How far the Orbit camera circles from Mario when you first choose it; the Radius control changes it from there.",
  },
  rigOrbitSpeed: {
    group: "Cinematic", label: "Orbit: speed to start with", value: 30,
    min: -720, max: 720, step: 1, unit: "deg/s",
    why: "How fast the Orbit camera circles when you first choose it, on the scene's own clock (paused, it stays put). Negative goes the other way.",
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

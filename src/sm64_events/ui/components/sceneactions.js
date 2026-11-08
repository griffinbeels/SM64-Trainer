// src/sm64_events/ui/components/sceneactions.js -- the Scene viewer's step to the next or previous
// action change (lab only).
//
// Xander's FrameAdvance runs a TAS until Mario's action changes, then pauses. Here the scene is
// already recorded, so the play bar's two outer step buttons (or [ and ]) jump straight to the frame
// where the action of whoever Follow names (the stage's Mario, or a stage ghost) last changed or next
// changes: the frame a dive, a kick or a wall kick begins. The play bar's readout names that action on
// the frame on screen in plain words ("Mario: Dive slide"), the name the rest of the Trainer gives it
// (memory/addresses.py action_label, sent by the lab).
//
// The lab reads every frame's action once (GET /api/scene-lab/actions: the stage's Mario from his
// path's reads, gMarioStates.action; a ghost from its clip's live_action; scene_session.actions) and
// the page keeps the answer for that scene, runner and length of scene. It goes over HTTP, so the
// socket's messages stay the pictures' and their questions.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
import { getJSON } from "../api.js";
import { Icon } from "./icons.js";
import { ghostName } from "./sceneghosts.js";

const html = htm.bind(h);

// The frames where the action changes: each frame with an action unlike the last known one before it.
export function actionChanges(actions) {
  const changes = [];
  let last = null;
  actions.forEach((action, frame) => {
    if (action == null) return;
    if (action !== last) changes.push(frame);
    last = action;
  });
  return changes;
}

// The next change after `frame` (direction 1) or the last one before it (-1), or null.
export function changeFrom(actions, frame, direction) {
  const changes = actionChanges(actions);
  if (direction > 0) return changes.find((at) => at > frame) ?? null;
  return [...changes].reverse().find((at) => at < frame) ?? null;
}

// An action's name with its first letter raised: "Dive slide".
export const actionWords = (name) => (name ? name.charAt(0).toUpperCase() + name.slice(1) : "");

const actionsKey = (link) => `${link.scene?.session}|${link.follow}|${link.scene?.frames.length}`;

// The actions of whoever Follow names in the open scene: asked once for each scene, runner and length.
export function loadActions(link) {
  const key = actionsKey(link);
  if (!link.scene || link.actionsAsked?.key === key) return;
  const asked = link.actionsAsked = { key, answer: null };
  const query = `session=${link.scene.session}&target=${encodeURIComponent(link.follow)}`;
  getJSON(`/api/scene-lab/actions?${query}`).then(
    (answer) => { asked.answer = answer; },
    (problem) => { asked.answer = { actions: [], names: {}, reason: problem.message }; },
  ).finally(() => { if (link.actionsAsked === asked) link.changed(); });
}

const answerOf = (link) => (link.actionsAsked?.key === actionsKey(link) ? link.actionsAsked.answer : null);

// ◀ or ▶: the scene pauses on the frame where the action last changed or next changes.
export function stepAction(link, direction, time) {
  const answer = answerOf(link);
  const to = answer ? changeFrom(answer.actions, link.frame, direction) : null;
  if (to == null) return link.sayBriefly(direction > 0 ? "No action change after this frame." : "No action change before this frame.");
  link.step(to - link.frame, time);
}

// The play bar's words for the frame on screen: the runner and its action, or why there is none.
export function actionLine(link) {
  const answer = answerOf(link);
  const who = link.follow === "stage" ? "Mario" : ghostName(link.ghosts, link.follow);
  if (!answer) return { text: `${who}: ...`, title: "Reading the actions..." };
  const action = answer.actions[link.frame];
  if (answer.reason) return { text: `${who}: no action`, title: answer.reason };
  return { text: `${who}: ${action ? actionWords(answer.names[String(action)]) : "no action here"}`, title: null };
}

// Whether the step to an action change can run: the actions are read and there are some.
export const actionsReady = (link) => !!answerOf(link)?.actions.length;

// The name's place is as wide as Mario and the longest action name the Trainer knows (memory/
// addresses.py action_label, 24 letters), whatever is on screen, so stepping never moves the buttons
// (viewer round 9's SteadyText); a longer name or a ghost's is cut short, whole on hover.
export const ACTION_SIZES = ["Mario: reading automatic dialog"];

// Reads the actions of whoever Follow names once per scene, runner and length (the play bar calls it). An
// effect only when what it asks about changes: Preact runs effects after an animation frame, and this page
// draws for every picture.
export function useActions(link) {
  useEffect(() => { loadActions(link); }, [link.scene, link.follow, link.scene?.frames.length]);
}

// The two buttons that step to the last or next action change (or [ and ]).
export function ActionButton({ link, direction }) {
  const back = direction < 0;
  return html`<button type="button" class="icon-button" disabled=${!actionsReady(link)}
      onclick=${(event) => stepAction(link, direction, event.timeStamp)}
      aria-label=${back ? "Back to the last action change" : "On to the next action change"}
      title=${back ? "Back to where this move began, or the one before ([)" : "On to where the next move begins (])"}>
    <${Icon} name=${back ? "actionBack" : "actionForward"} size=${16} /></button>`;
}

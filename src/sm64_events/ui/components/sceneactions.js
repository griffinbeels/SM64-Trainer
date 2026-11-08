// src/sm64_events/ui/components/sceneactions.js -- the Scene viewer's step to the next or previous
// action change (lab only).
//
// Xander's FrameAdvance runs a TAS until Mario's action changes, then pauses. Here the scene is
// already recorded, so ◀ and ▶ (or [ and ]) jump straight to the frame where the action of whoever
// Follow names (the stage's Mario, or a stage ghost) last changed or next changes: the frame a dive,
// a kick or a wall kick begins. The block names that action on the frame on screen in plain words
// ("Mario: Dive slide"), the name the rest of the Trainer gives it (memory/addresses.py action_label,
// sent by the lab).
//
// The lab reads every frame's action once (the socket's "actions" question: the stage's Mario from his
// path's reads, gMarioStates.action; a ghost from its clip's live_action; scene_session.actions) and
// the page keeps the answer for that scene, runner and length of scene.
import { h } from "preact";
import { useEffect } from "preact/hooks";
import htm from "htm";
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

// The actions of whoever Follow names in the open scene: asked once for each scene, runner and length,
// once the scene's socket is open.
export function loadActions(link) {
  const key = actionsKey(link);
  if (!link.scene || link.ws?.readyState !== WebSocket.OPEN || link.actionsAsked?.key === key) return;
  const asked = link.actionsAsked = { key, answer: null };
  link.ask("actions", { target: link.follow }).then((answer) => {
    if (link.actionsAsked !== asked) return;
    asked.answer = answer.kind === "actions" ? answer : { actions: [], names: {}, reason: answer.reason };
    link.changed();
  });
}

const answerOf = (link) => (link.actionsAsked?.key === actionsKey(link) ? link.actionsAsked.answer : null);

// ◀ or ▶: the scene pauses on the frame where the action last changed or next changes.
export function stepAction(link, direction, time) {
  const answer = answerOf(link);
  const to = answer ? changeFrom(answer.actions, link.frame, direction) : null;
  if (to == null) return link.sayBriefly(direction > 0 ? "No action change after this frame." : "No action change before this frame.");
  link.step(to - link.frame, time);
}

// The row's own words: the runner and the action on screen, or why there is none.
function actionLine(link, answer) {
  const who = link.follow === "stage" ? "Mario" : ghostName(link.ghosts, link.follow);
  if (!answer) return { text: `${who}: ...`, title: "Reading the actions..." };
  const action = answer.actions[link.frame];
  if (answer.reason) return { text: `${who}: no action`, title: answer.reason };
  return { text: `${who}: ${action ? actionWords(answer.names[String(action)]) : "no action here"}`, title: null };
}

export function ActionStep({ link }) {
  useEffect(() => { loadActions(link); });
  const answer = answerOf(link), line = actionLine(link, answer), usable = !!answer?.actions.length;
  return html`<div class="scene-lab-action" role="group" aria-label="Action">
    <button type="button" disabled=${!usable} onclick=${(event) => stepAction(link, -1, event.timeStamp)}
        aria-label="Back to the last action change" title="Back to the frame this action began, or the one before ([)">
      <${Icon} name="stepBack" size=${14} /></button>
    <output class="scene-lab-action-name" title=${line.title}>${line.text}</output>
    <button type="button" disabled=${!usable} onclick=${(event) => stepAction(link, 1, event.timeStamp)}
        aria-label="On to the next action change" title="On to the frame the next action begins (])">
      <${Icon} name="stepForward" size=${14} /></button>
  </div>`;
}

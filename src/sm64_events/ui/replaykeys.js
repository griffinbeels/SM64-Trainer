// One native replay owns arrow shortcuts: the last opened or interacted player.
// Each player's hold has its own host, so changing focus cannot resume or
// cancel another player's hold through a shared window timer.
import { startHold, stopHold } from "./holdrepeat.js";
import { replayShuttle } from "./replayshuttle.js";
import { reviewCommand } from "./reviewcommands.js";

const players = new Map();
let active = null;
let heldK = false;

function activate(root) {
  if (active !== root) heldK = false;
  if (active && active !== root) { stopHold(active); players.get(active)?.shuttle.stop(); }
  active = root;
}

function direction(key) {
  return key === "ArrowLeft" ? -1 : key === "ArrowRight" ? 1 : 0;
}

function typing(node) {
  return node?.isContentEditable
    || /^(INPUT|TEXTAREA|SELECT)$/.test(node?.tagName || "");
}

function down(event) {
  const player = players.get(active), dir = direction(event.key);
  const key = event.key.toLowerCase();
  const dialog = event.target?.closest?.('[role="dialog"]');
  const ownDialog = active?.closest?.('[role="dialog"]');
  if (!player || (!dir && event.key !== "ArrowDown" && event.key !== " " && !["j", "k", "l", "i", "o", "x"].includes(key))
      || event.defaultPrevented || typing(event.target)
      || (dialog && dialog !== ownDialog)
      || (event.key === " " && event.target?.closest?.('button,a,[role="button"]'))
      || event.metaKey || event.ctrlKey || event.altKey) return;
  event.preventDefault();
  event.stopPropagation();
  if (event.repeat) return;
  if (["i", "o", "x"].includes(key)) {
    stopHold(active); player.shuttle.stop();
    reviewCommand(player.video, key === "i" ? (event.shiftKey ? "start" : "in") : key === "o" ? "out" : "clear");
    return;
  }
  if (["j", "k", "l"].includes(key)) {
    stopHold(active);
    if (key === "k") { heldK = true; player.shuttle.pause(); }
    else if (heldK) { player.shuttle.pause(); startHold(active, () => player.step(key === "j" ? -1 : 1)); }
    else player.shuttle.run(key === "j" ? -1 : 1);
    return;
  }
  player.shuttle.stop();
  if (event.key === " ") {
    stopHold(active); player.toggle?.(); return;
  }
  if (!dir) {
    stopHold(active);
    player.toStart();
  } else {
    startHold(active, () => player.step(dir), { onPress: player.onPress });
  }
}

function up(event) {
  const key = event.key.toLowerCase();
  if ((direction(event.key) || ["j", "k", "l"].includes(key)) && active) stopHold(active);
  if (key === "k") heldK = false;
}

function blur() {
  heldK = false;
  if (active) { stopHold(active); players.get(active)?.shuttle.stop(); }
}

function outside(event) {
  if (active && !active.contains(event.target)) activate(null);
}

export function watchReplayKeys(root, player) {
  if (!root) return () => {};
  if (!players.size) {
    globalThis.addEventListener("keydown", down);
    globalThis.addEventListener("keyup", up);
    globalThis.addEventListener("blur", blur);
    globalThis.addEventListener("pointerdown", outside, true);
    globalThis.addEventListener("focusin", outside, true);
  }
  const shuttle = replayShuttle(player.video);
  players.set(root, { ...player, shuttle });
  activate(root);
  const claim = () => activate(root);
  root.addEventListener("pointerdown", claim);
  root.addEventListener("focusin", claim);
  return () => {
    stopHold(root);
    shuttle.dispose();
    root.removeEventListener("pointerdown", claim);
    root.removeEventListener("focusin", claim);
    players.delete(root);
    if (active === root) { active = [...players.keys()].at(-1) || null; heldK = false; }
    if (!players.size) {
      globalThis.removeEventListener("keydown", down);
      globalThis.removeEventListener("keyup", up);
      globalThis.removeEventListener("blur", blur);
      globalThis.removeEventListener("pointerdown", outside, true);
      globalThis.removeEventListener("focusin", outside, true);
    }
  };
}

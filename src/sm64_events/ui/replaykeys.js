// One native replay owns arrow shortcuts: the last opened or interacted player.
// Each player's hold has its own host, so changing focus cannot resume or
// cancel another player's hold through a shared window timer.
import { startHold, stopHold } from "./holdrepeat.js";

const players = new Map();
let active = null;

function activate(root) {
  if (active && active !== root) stopHold(active);
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
  if (!player || (!dir && event.key !== "ArrowDown") || event.repeat
      || event.defaultPrevented || typing(event.target)
      || event.target?.closest?.('[role="dialog"]')
      || event.metaKey || event.ctrlKey || event.altKey) return;
  event.preventDefault();
  if (!dir) {
    stopHold(active);
    player.toStart();
  } else {
    startHold(active, () => player.step(dir), { onPress: player.onPress });
  }
}

function up(event) {
  if (direction(event.key) && active) stopHold(active);
}

function blur() {
  if (active) stopHold(active);
}

export function watchReplayKeys(root, player) {
  if (!root) return () => {};
  if (!players.size) {
    globalThis.addEventListener("keydown", down);
    globalThis.addEventListener("keyup", up);
    globalThis.addEventListener("blur", blur);
  }
  players.set(root, player);
  activate(root);
  const claim = () => activate(root);
  root.addEventListener("pointerdown", claim);
  root.addEventListener("focusin", claim);
  return () => {
    stopHold(root);
    root.removeEventListener("pointerdown", claim);
    root.removeEventListener("focusin", claim);
    players.delete(root);
    if (active === root) active = [...players.keys()].at(-1) || null;
    if (!players.size) {
      globalThis.removeEventListener("keydown", down);
      globalThis.removeEventListener("keyup", up);
      globalThis.removeEventListener("blur", blur);
    }
  };
}

// Private player codes stay in the deliberate request/clipboard flow only.
import { localFetch } from "./api.js";

const ROOT = "/api/multiplayer";
const PHASES = {
  idle: "Friends", starting: "Starting room…", hosting: "Your room", joining: "Joining…",
  connected: "Practice room", reconnecting: "Reconnecting…", leaving: "Leaving…", error: "Connection problem",
};
const MEMBER_STATES = { playing: "Playing", paused: "Paused", waiting: "Waiting for game", reconnecting: "Reconnecting…",
  disconnected: "Connection lost" };

function badgeInfo(invitations, room, count, phase) {
  if (["reconnecting", "error"].includes(phase)) return { badge: "!", badgeLabel: PHASES[phase] };
  if (["starting", "joining", "leaving"].includes(phase)) return { badge: "…", badgeLabel: PHASES[phase] };
  if (invitations.length) return { badge: String(invitations.length),
    badgeLabel: `${invitations.length} ${invitations.length === 1 ? "invite" : "invites"}` };
  if (room) return { badge: `${count}/4`, badgeLabel: `${count} of 4 players` };
  return { badge: "", badgeLabel: "Friends" };
}

export function friendsModel(snapshot) {
  const data = snapshot || {}, room = data.room;
  const members = room ? room.members : [];
  const invitations = data.invitations || [];
  const count = members.length;
  const phase = data.phase || "idle";
  const transitioning = ["starting", "joining", "leaving"].includes(phase);
  return {
    phase, title: PHASES[phase] || "Friends", members, invitations, count,
    slots: Array.from({ length: 4 }, (_, index) => members[index] || null),
    full: count >= 4, room: Boolean(room), owner: Boolean(room && room.owner),
    available: Boolean(data.available), transitioning,
    canInvite: Boolean(data.can_invite) && count < 4 && !transitioning,
    ...badgeInfo(invitations, room, count, phase),
  };
}

export function memberState(member) { return MEMBER_STATES[member.state] || "Waiting for game"; }

function failure(action, status) {
  if (status === 401 || status === 403) return "Sign in to your Trainer account, then try again.";
  if (action === "invite") return "Couldn’t invite that player. Check their player code and try again.";
  if (action === "join") return "That invite couldn’t be accepted. Ask your friend for a new invite.";
  if (action === "identity") return "Couldn’t get your player code. Try again.";
  return "Friends couldn’t finish that action. Try again.";
}

export async function friendsRequest(action = "status", body, options = {}) {
  const reading = action === "status";
  let response;
  try {
    response = await localFetch(reading ? ROOT : `${ROOT}/${action}`, reading
      ? { cache: "no-store", signal: options.signal }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  } catch (problem) {
    if (problem.name === "AbortError") throw problem;
    // Do not forward errors containing input/identity data to diagnostics.
    throw new Error(reading ? "Friends is unavailable. Try again." : failure(action));
  }
  if (!response.ok) throw new Error(reading ? "Friends is unavailable. Try again." : failure(action, response.status));
  try { return await response.json(); }
  catch { throw new Error(reading ? "Friends is unavailable. Try again." : failure(action)); }
}

export function responseSnapshot(result) {
  return result?.snapshot?.phase ? result.snapshot : result?.phase ? result : null;
}

export function readFriends(root) {
  const surface = root.matches?.(".friends-surface") ? root : root.querySelector(".friends-surface");
  if (!surface) return null;
  return { surface: "friends", phase: surface.dataset.phase,
    members: surface.querySelectorAll(".friends-seat.is-occupied").length,
    invitations: surface.querySelectorAll(".friends-invitation").length };
}

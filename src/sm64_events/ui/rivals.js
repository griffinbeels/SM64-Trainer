// Pure view rules for the server-owned rival roster. Never grade a recording here.
export function endpoint(options, pb, name, limits) {
  const last = limits.last_step;
  if (options.mode === "fixed") return options[name === "min" ? "min_step" : "max_step"];
  return Math.min(last, (pb?.step ?? 0) + Math.max(1, options[name === "min" ? "min_ahead" : "max_ahead"]));
}

export function rangeDraft(options, pb, limits) {
  const draft = {...options};
  if (draft.mode === "adaptive") {
    const cap = pb == null ? limits.max_ahead : Math.min(limits.max_ahead, limits.last_step - pb.step);
    const floor = cap === 0 ? 0 : 1;
    draft.min_ahead = Math.max(floor, Math.min(cap, draft.min_ahead));
    draft.max_ahead = Math.max(draft.min_ahead, Math.min(cap, draft.max_ahead));
  }
  return draft;
}

export function stepRange(draft, pb, limits, name, delta) {
  const next = {...draft};
  const adaptive = draft.mode === "adaptive";
  const key = adaptive ? `${name}_ahead` : `${name}_step`;
  const lowKey = adaptive ? "min_ahead" : "min_step";
  const highKey = adaptive ? "max_ahead" : "max_step";
  const cap = adaptive ? (pb == null ? limits.max_ahead : Math.min(limits.max_ahead, limits.last_step - pb.step)) : limits.last_step;
  const floor = adaptive && cap > 0 ? 1 : 0;
  const lo = name === "max" ? draft[lowKey] : floor;
  const hi = name === "min" ? draft[highKey] : cap;
  next[key] = Math.max(lo, Math.min(hi, draft[key] + delta));
  return next;
}

export function groupPlayers(rows) {
  const players = new Map();
  for (const row of rows) {
    const group = players.get(row.player_id) || {id:row.player_id, name:row.player_name, recordings:[]};
    group.recordings.push(row);
    players.set(row.player_id, group);
  }
  for (const group of players.values()) group.recordings.sort((a,b)=>a.frames-b.frames || a.id.localeCompare(b.id));
  return [...players.values()];
}

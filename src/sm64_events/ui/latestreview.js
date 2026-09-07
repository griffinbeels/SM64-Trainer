import { entityKey } from "./entitysection.js";

export function reviewRows(view) {
  return [...(view?.stars || []), ...(view?.segments || [])].flatMap(section =>
    (section.attempts || []).filter(attempt => !attempt.imported && attempt.ended_utc)
      .map(attempt => ({ ...attempt, entity: section, entityKey: entityKey(section),
        label: section.segment_name || section.name || section.star_name || "Attempt" })))
    .concat((view?.unassigned || []).filter(a => !a.imported && a.ended_utc)
      .map(attempt => ({ ...attempt, label: "Unassigned attempt", entityKey: null })));
}

export function newestReview(rows, preferredKey = null) {
  return [...rows].sort((a, b) =>
    Date.parse(b.ended_utc) - Date.parse(a.ended_utc)
    || Number(b.entityKey === preferredKey) - Number(a.entityKey === preferredKey)
    || String(a.entityKey).localeCompare(String(b.entityKey))
    || a.id - b.id)[0] || null;
}

export function reviewFromEvent(event) {
  const p = event.payload;
  return { ...p, id: p.attempt_id, ended_utc: event.timestamp_utc,
    entity: p, entityKey: entityKey(p),
    label: p.segment_name || p.star_name || "Attempt" };
}

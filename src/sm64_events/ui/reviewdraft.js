// Only unacknowledged edits live here. A server-lifetime token prevents a
// refreshed tab from resurrecting an unsaved replay's old-session state.
const prefix = "sm64.reviewDraft.";
let fallbackSequence = 0;
let fallbackClient;

export function retainDraft(id, session, state) {
  if (!session) return null;
  try {
    const client = sessionStorage.getItem(`${prefix}client`)
      || crypto.randomUUID().replaceAll("-", "");
    const sequence = Number(sessionStorage.getItem(`${prefix}sequence`) || 0) + 1;
    sessionStorage.setItem(`${prefix}client`, client);
    sessionStorage.setItem(`${prefix}sequence`, String(sequence));
    const edit = `${session}/${client}/${sequence}`;
    sessionStorage.setItem(`${prefix}${id}`, JSON.stringify({session, state, edit}));
    return edit;
  } catch {
    fallbackClient ||= crypto.randomUUID().replaceAll("-", "");
    return `${session}/${fallbackClient}/${++fallbackSequence}`;
  }
}

export function readDraft(id, session) {
  if (!session) return null;
  try {
    const key = `${prefix}${id}`, raw = sessionStorage.getItem(key);
    if (!raw || raw.length > 40_000) return null;
    const draft = JSON.parse(raw);
    if (draft.session !== session) { sessionStorage.removeItem(key); return null; }
    return draft.state && typeof draft.edit === "string" ? draft : null;
  } catch { return null; }
}

export function acknowledgeDraft(id, edit) {
  try {
    const key = `${prefix}${id}`, raw = sessionStorage.getItem(key);
    if (raw && JSON.parse(raw).edit === edit) sessionStorage.removeItem(key);
  } catch { /* Storage is optional; the server remains authoritative. */ }
}

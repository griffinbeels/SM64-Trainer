// Ordinary picks follow chronology; assembling OR starts pins the chosen roles.
import { useState } from "preact/hooks";

const EMPTY = { ids: [], starts: [], boundaries: null };
const chronological = (ids) => [...ids].sort((left, right) => left - right);

function removePrimary(selection) {
  const { ids, starts, boundaries } = selection;
  if (!starts.length) return EMPTY;
  const primary = starts[0];
  return { ids: [primary, ...ids.filter((id) => id !== boundaries.primary_start_id && id !== primary)],
    starts: starts.slice(1), boundaries: { ...boundaries,
      primary_start_id: primary, infer_walk: false } };
}

function pinnedPick(selection, id) {
  const { ids, starts, boundaries } = selection;
  const { primary_start_id: primary, finish_id: finish } = boundaries;
  if (id === primary) return removePrimary(selection);
  if (id === finish) return { ...selection, ids: ids.filter((item) => item !== id),
    boundaries: { ...boundaries, finish_id: null } };
  if (starts.includes(id)) return { ...selection,
    ids: ids.filter((item) => item !== id), starts: starts.filter((item) => item !== id) };
  if (ids.includes(id)) return { ...selection, ids: ids.filter((item) => item !== id) };
  if (finish == null) return { ...selection, ids: [...ids, id],
    boundaries: { ...boundaries, finish_id: id } };
  const middles = ids.filter((item) => item !== primary && item !== finish);
  return { ...selection, ids: [primary, ...chronological([...middles, id]), finish] };
}

function ordinaryPick(selection, id) {
  if (selection.boundaries) return pinnedPick(selection, id);
  const next = selection.ids.includes(id)
    ? selection.ids.filter((item) => item !== id) : chronological([...selection.ids, id]);
  return { ids: next, starts: [], boundaries: null };
}

export function useRecordedSelection(onChange) {
  const [selection, setSelection] = useState(EMPTY);
  const [addingStart, setAddingStart] = useState(false);
  const [pickNotice, setPickNotice] = useState(null);

  function toggleRow(row) {
    setPickNotice(null);
    let next;
    if (addingStart) {
      const { primary_start_id: primary, finish_id: finish } = selection.boundaries;
      if (row.id === primary || row.id === finish) {
        setPickNotice(row.id === primary
          ? "This is already the primary start. Pick another moment."
          : "This is the finish. Pick a different moment as another start.");
        return;
      }
      const starts = selection.starts.includes(row.id)
        ? selection.starts.filter((id) => id !== row.id)
        : chronological([...selection.starts, row.id]);
      next = { ...selection, starts };
    } else next = ordinaryPick(selection, row.id);
    setSelection(next);
    if (!next.ids.length) setAddingStart(false);
    onChange(next.ids, next.starts, next.boundaries);
  }

  function resetPicks() {
    setSelection(EMPTY);
    setAddingStart(false); setPickNotice(null);
  }

  function toggleStartMode() {
    if (!selection.boundaries) setSelection({ ...selection,
      boundaries: { primary_start_id: selection.ids[0],
        finish_id: selection.ids.length > 1 ? selection.ids.at(-1) : null } });
    setAddingStart(!addingStart); setPickNotice(null);
  }

  return { pickedIds: selection.ids, extraStartIds: selection.starts,
    boundaries: selection.boundaries, addingStart, pickNotice,
    toggleRow, resetPicks, toggleStartMode };
}

export function recordedPickSummary(ids, starts, boundaries = null) {
  if (!ids.length) return "Nothing picked yet — newest is at the top.";
  if (boundaries) {
    const steps = ids.filter((id) => id !== boundaries.primary_start_id
      && id !== boundaries.finish_id && !starts.includes(id)).length;
    return `${starts.length + 1} starts, ${steps} selected steps`
      + (boundaries.finish_id != null ? ", 1 finish." : ". Pick the finish next.");
  }
  return ids.length === 1 ? "1 moment picked. Pick the one it finished on."
    : `${ids.length} moments picked.`;
}

export function hasRecordedFinish(ids, boundaries) {
  return ids.length > 1 && (!boundaries || boundaries.finish_id != null);
}

export function recordedSynthesisQuery(ids, starts, boundaries) {
  return `/api/segments/synthesize?ids=${ids.join(",")}`
    + (starts.length ? `&start_ids=${starts.join(",")}` : "")
    + (boundaries ? `&primary_start_id=${boundaries.primary_start_id}`
      + `&finish_id=${boundaries.finish_id}` : "")
    + (boundaries?.infer_walk === false ? "&infer_walk=false" : "");
}

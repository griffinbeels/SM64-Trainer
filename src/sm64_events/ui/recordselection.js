// Selection roles in the recorder: a representative path plus independent OR starts.
import { useState } from "preact/hooks";

export function useRecordedSelection(onChange) {
  const [pickedIds, setPickedIds] = useState([]);
  const [extraStartIds, setExtraStartIds] = useState([]);
  const [addingStart, setAddingStart] = useState(false);
  const [pickNotice, setPickNotice] = useState(null);

  function toggleRow(row) {
    setPickNotice(null);
    if (addingStart) {
      if (row.id === pickedIds[0] || row.id === pickedIds.at(-1)) {
        setPickNotice(row.id === pickedIds[0]
          ? "This is already the primary start. Pick another moment."
          : "This is the finish. Pick a different moment as another start.");
        return;
      }
      const starts = extraStartIds.includes(row.id)
        ? extraStartIds.filter((id) => id !== row.id)
        : [...extraStartIds, row.id].sort((left, right) => left - right);
      setExtraStartIds(starts);
      onChange(pickedIds, starts);
      return;
    }
    if (extraStartIds.includes(row.id)) {
      const starts = extraStartIds.filter((id) => id !== row.id);
      const ids = pickedIds.filter((id) => id !== row.id);
      setPickedIds(ids); setExtraStartIds(starts);
      onChange(ids, starts);
      return;
    }
    const next = pickedIds.includes(row.id)
      ? pickedIds.filter((id) => id !== row.id)
      : [...pickedIds, row.id].sort((left, right) => left - right);
    setPickedIds(next);
    const starts = next.length ? extraStartIds.filter(
      (id) => id !== next[0] && id !== next.at(-1)) : [];
    setExtraStartIds(starts);
    if (next.length === 0) setAddingStart(false);
    onChange(next, starts);
  }

  function resetPicks() {
    setPickedIds([]); setExtraStartIds([]);
    setAddingStart(false); setPickNotice(null);
  }

  function toggleStartMode() {
    setAddingStart(!addingStart); setPickNotice(null);
  }

  return { pickedIds, extraStartIds, addingStart, pickNotice,
    toggleRow, resetPicks, toggleStartMode };
}

export function recordedPickSummary(pickedIds, starts) {
  if (pickedIds.length === 0) return "Nothing picked yet — newest is at the top.";
  if (starts.length) {
    const steps = pickedIds.slice(1, -1).filter((id) => !starts.includes(id)).length;
    return `${starts.length + 1} starts, ${steps} selected steps`
      + (pickedIds.length > 1 ? ", 1 finish." : ". Pick the finish next.");
  }
  return pickedIds.length === 1 ? "1 moment picked. Pick the one it finished on."
    : `${pickedIds.length} moments picked.`;
}

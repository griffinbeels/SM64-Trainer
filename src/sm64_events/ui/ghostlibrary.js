// Collection selection is independent of the small set raced at once.
export function rowId(row) {
  return row.import_id || `attempt:${row.attempt_id}`;
}

export function belongsHere(row, target) {
  return Boolean(target && row.kind === target.kind && row.entity_key === target.entity_key);
}

export function libraryRows(rows, filters, target) {
  const query = filters.query.trim().toLocaleLowerCase();
  const visible = rows.filter(row =>
    (filters.source === "all" || row.source === filters.source) &&
    (!filters.targetOnly || belongsHere(row, target)) &&
    (filters.practice || (!row.practice && row.outcome === "success")) &&
    (!query || [row.target_name, row.strat, row.author].some(value =>
      String(value || "").toLocaleLowerCase().includes(query))));
  return visible.sort((a, b) => filters.sort === "recent"
    ? String(b.recorded_utc).localeCompare(String(a.recorded_utc)) || rowId(a).localeCompare(rowId(b))
    : (a.frames ?? Infinity) - (b.frames ?? Infinity) || rowId(a).localeCompare(rowId(b)));
}

export function selectionOf(rows) {
  return {attempts: rows.filter(row => row.source === "local").map(row => row.attempt_id),
    imports: rows.filter(row => row.source === "import").map(row => row.import_id)};
}

export function raceChoice(rows, target, pb = true) {
  return {pb, ...selectionOf(rows.filter(row => row.available && belongsHere(row, target)))};
}

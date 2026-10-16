// Collection selection is independent of the small set raced at once.
export function rowId(row) {
  return row.import_id || (row.attempt_id != null ? `attempt:${row.attempt_id}` : row.id);
}

export function belongsHere(row, target) {
  return Boolean(target && row.kind === target.kind && row.entity_key === target.entity_key);
}

export function raceContextReason(opening, liveTarget) {
  return belongsHere(opening, liveTarget) ? "" : "Make this star or segment your practice target, then reopen the library to race its ghosts.";
}

export function libraryRows(rows, filters, target) {
  const query = filters.query.trim().toLocaleLowerCase();
  const visible = rows.filter(row =>
    (filters.source === "all" || row.source === filters.source) &&
    (!filters.targetOnly || belongsHere(row, target)) &&
    (!filters.target || belongsHere(row, filters.target)) &&
    (filters.practice || (!row.practice && row.outcome === "success")) &&
    (!query || [row.target_name, row.strat, row.author].some(value =>
      String(value || "").toLocaleLowerCase().includes(query))));
  return visible.sort((a, b) => filters.sort === "recent"
    ? String(b.recorded_utc).localeCompare(String(a.recorded_utc)) || rowId(a).localeCompare(rowId(b))
    : (a.frames ?? Infinity) - (b.frames ?? Infinity) || rowId(a).localeCompare(rowId(b)));
}

export function selectionOf(rows) {
  return {attempts: rows.filter(row => row.source === "local" && row.attempt_id != null).map(row => row.attempt_id),
    imports: rows.filter(row => row.source === "import").map(row => row.import_id)};
}

// Keep the opening section separate from the changing live practice target.
export function openingTarget(entityKey, fallback) {
  const [kind, ...parts] = String(entityKey || "").split(":");
  if (kind === "star" && parts.length === 2)
    return {kind, entity_key: `${parts[0]}-${parts[1]}`};
  if (kind === "segment" && parts.length === 1)
    return {kind, entity_key: parts[0]};
  return fallback ? {kind: fallback.kind, entity_key: fallback.entity_key} : null;
}

export function uniqueRows(rows) {
  const byId = new Map();
  for (const row of rows) if (!byId.has(rowId(row))) byId.set(rowId(row), row);
  return [...byId.values()];
}

// Timers and ROM versions have independent overall winners. An unavailable
// winner retains its identity; availability never promotes a slower run.
export function bestGroups(rows, target) {
  const groups = new Map();
  for (const row of rows.filter(item => belongsHere(item, target))) {
    const key = `${row.clock}:${row.game_version || "unknown"}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return [...groups.entries()].map(([key, members]) => {
    const sorted = [...members].sort((a, b) => a.frames - b.frames || rowId(a).localeCompare(rowId(b)));
    const fastest = sorted[0].frames;
    const winners = sorted.filter(row => row.frames === fastest);
    return {key, rows: sorted, winners, defaultRow: winners.find(row => row.available) || null};
  });
}

export function overallSelection(rows, target) {
  return new Set(bestGroups(rows, target).flatMap(group => group.defaultRow ? [rowId(group.defaultRow)] : []));
}

export function resolveShortcut(catalog, criteria) {
  return libraryRows(criteria.bests ? catalog.bests || [] : catalog.ghosts || [], criteria, null)
    .filter(row => row.available);
}

export function importSelection(rows, mode, includeNoBest = false) {
  return new Set(rows.filter(row => row.importable && !row.duplicate &&
    (mode === "all" || row.comparison === "faster" || includeNoBest && row.comparison === "no_best"))
    .map(row => row.id || row.import_id));
}

export function raceChoice(rows, target, pb = true) {
  return {pb, ...selectionOf(rows.filter(row => row.available && belongsHere(row, target)))};
}

// The current saved PB has one racing slot even when its recording also
// appears as a normal library row. Historical strategy winners keep theirs.
export function raceSelection(rows, candidates, target, currentPb, defaultPb = true) {
  const pbRow = candidates.find(row => row.source === "local" && row.available &&
    row.attempt_id === currentPb?.attempt_id && belongsHere(row, target));
  const pb = pbRow ? rows.some(row => rowId(row) === rowId(pbRow)) : defaultPb;
  const recordings = pbRow ? rows.filter(row => rowId(row) !== rowId(pbRow)) : rows;
  return raceChoice(recordings, target, pb);
}

export function raceRecordingLimit(catalog, pb) {
  return Math.min(catalog.max_rivals ?? catalog.max_race, catalog.max_race - Number(pb));
}

export function raceFits(choice, cap, maxRivals = cap) {
  const rivals = choice.attempts.length + choice.imports.length;
  const slots = rivals + Number(choice.pb);
  return slots > 0 && slots <= cap && rivals <= maxRivals;
}

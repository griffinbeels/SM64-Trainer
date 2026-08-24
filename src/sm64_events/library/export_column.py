"""Your PBs, laid out as the Ultimate Sheet's own column -- one line per live
worksheet row, ready to paste back into the sheet next to everyone else's.

The reverse direction from `import_runner.py`: that reads a runner's column
IN, landing it as PBs; this writes YOUR PBs OUT, formatted the way the sheet
itself expects a time to be typed. `column_lines` walks the same `read_rows`
output the payload's `targets` were built from, in lockstep -- a new target
opens exactly where a `SheetRow.opens_target` row does, so the two never need
to agree on row numbers, only on ORDER. That is why `rows` and `payload` must
come from the SAME fetch: a payload rebuilt from a newer sheet would open its
targets in a different order than an older `rows` list, and the walk would
silently pair the wrong ones.

Per row, two questions the caller answers, matching the same order of
authority `server/import_api.py::sheet_row_placer` vouches import rows with
(so the export can never claim a row the import would not land too):

  1. an approach on a STAR target carrying a `matched_strategy` (the vetted
     pairing `library/adopt.py::stamp_matches` writes) resolves on that
     strategy, directly;
  2. anything else -- a subsection, a castle movement, a Bowser row -- goes
     through `place(target, item, kind)`, the caller's placer; unplaced,
     it is blank, never the sheet's own approach name guessed as a strategy.

Both branches end at `resolve(entity_key, strat_tag, timer_mode, version) ->
cs | None` -- the caller's PB lookup, including the game_version check (a PB
set on the other ROM must not print under an explicitly-versioned row).

The round trip is ONE-WAY, deliberately: a star approach `import_runner.py`
lands under the SHEET's own name -- no vetted `matched_strategy`, and
`place` has nothing to place either, since a star target already carries its
own entity key and never reaches the placer's Bowser/name-match paths --
exports BLANK here. This door only ever resolves a strategy it can look up a
PB under, and a name borrowed straight from the sheet's own approach label is
not a strategy this database has ever heard of."""
from sm64_events.library.sheet import base_name


def sheet_time(centiseconds: int) -> str:
    """The sheet's own notation for typing a time in: `43.70`, `1:21.00` --
    minutes only once the value reaches 60s, centiseconds always two digits.

    Not `import_runner._sheet_time`'s `m'ss"cc`, which is for the rejected-row
    list; this is what a sheet CELL expects, so a pasted column round-trips
    through `sheet.parse_time` unchanged."""
    minutes, rest = divmod(int(centiseconds), 6000)
    seconds, cents = divmod(rest, 100)
    body = f"{minutes}:{seconds:02d}" if minutes else str(seconds)
    return f"{body}.{cents:02d}"


def _find_item(row, target):
    """The payload approach/subsection this row's own worksheet text names,
    inside its own target -- an approach by its version-stripped name (the
    payload merges a (JP)/(US) pair under one), a subsection by its raw one
    (the payload never strips those)."""
    if target is None:
        return None
    if row.kind == "approach":
        name, collection = base_name(row.label), target.get("approaches")
    else:
        name, collection = row.label, target.get("subsections")
    for item in (collection or []):
        if item.get("name") == name:
            return item
    return None


def _line_for(row, target, resolve, place) -> str:
    item = _find_item(row, target)
    if item is None:
        return ""
    entity_key = (target or {}).get("entity_key") or ""
    if (row.kind == "approach" and entity_key.startswith("star:")
            and item.get("matched_strategy")):
        cs = resolve(entity_key, item["matched_strategy"], "igt", row.version)
    elif place is not None:
        placed = place(target, item, row.kind)
        if placed is None:
            return ""
        placed_entity, timer_mode, strategy = placed
        strategy = strategy or item.get("matched_strategy") or item.get("name")
        cs = resolve(placed_entity, strategy, timer_mode, row.version)
    else:
        return ""
    return sheet_time(cs) if cs is not None else ""


def column_lines(rows, payload, resolve, place=None) -> list:
    """One string per worksheet row, row 2 through the last data row --
    `""` wherever nothing maps: a header or spacer (no `SheetRow` at that
    row number at all), or a row whose text names nothing in its own target,
    or one `resolve`/`place` refuses.

    `rows` is `library/sheet.py::read_rows`'s output; `payload` is the
    library payload those same rows built (`library/build.py::build`).
    `place(target, item, kind) -> (entity_key, timer_mode, strat_tag) | None`
    is optional -- omit it (or let it answer `None`) and every row that is
    not a matched-strategy star approach goes blank, exactly like an import
    with no placer would drop it."""
    if not rows:
        return []
    targets = payload.get("targets") or []
    by_row = {row.row: row for row in rows}
    last_row = max(by_row)
    lines = []
    target_index = -1
    for row_number in range(2, last_row + 1):
        row = by_row.get(row_number)
        if row is None:
            lines.append("")
            continue
        if row.opens_target:
            target_index += 1
        target = (targets[target_index]
                  if 0 <= target_index < len(targets) else None)
        lines.append(_line_for(row, target, resolve, place))
    return lines

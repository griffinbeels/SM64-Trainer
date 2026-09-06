"""Your PBs, laid out as the Ultimate Sheet's own column -- one line per live
worksheet row, ready to paste back into the sheet next to everyone else's.

The reverse direction from `import_runner.py`: that reads a runner's column
IN, landing it as PBs; this writes YOUR PBs OUT, formatted the way the sheet
itself expects a time to be typed. The two are ONE round trip, and the rule
they share is that **this door prints a time exactly where that door would
land one** -- same order of authority, same strategy name. A row the import
drops exports blank; a row the import lands under a name exports whatever PB
is filed under that name.

`column_lines` walks the same `read_rows` output the payload's `targets` were
built from, in lockstep -- one `SheetRow.opens_target` row opens one BLOCK.
A block is usually one target, and is more than one wherever
`library/build.py::_apply_splits` carved a second entity out of a sheet
heading that holds two of ours: those carved targets carry `split_from` and
have no opening row of their own. Counting them as blocks-of-their-own is the
bug that shipped in round 18 -- the Princess's Secret Slide split added a
target with no row, so every row after it (214 of 803, the whole tail of the
sheet) was read against its NEIGHBOUR's target and exported blank. That is
why `rows` and `payload` must come from the SAME fetch, too: a payload
rebuilt from a newer sheet opens its blocks in a different order than an
older `rows` list.

Per row, the caller answers the same two questions in the same order
`import_runner.py::candidates_for` asks them, through the same placer
(`server/import_api.py::sheet_row_placer`):

  1. `place(target, item, kind)` -- the row's explicit link to a segment he
     built, the name-match an entity-less target gets unasked, the seed_key
     behind a sheet Bowser id. Whatever it names wins;
  2. failing that, an approach on a STAR target lands on that star, under
     the vetted `matched_strategy` where `library/adopt.py::stamp_matches`
     wrote one and otherwise under the sheet's own approach name.

Both branches end at `resolve(entity_key, strat_tag, timer_mode, version) ->
cs | None` -- the caller's PB lookup, including the game_version check (a PB
set on the other ROM must not print under an explicitly-versioned row).

That second fallback is what makes the round trip close, and it was refused
here until round 19 on the reasoning that a name borrowed from the sheet "is
not a strategy this database has ever heard of". It is: the import files a
star row under `matched_strategy or the sheet's own name`, so refusing the
name half meant a column he had just imported exported 0 of 803 lines. The
fallback can never print a WRONG time either -- a name this database has not
heard of resolves to `None` and the line stays blank."""
from sm64_events.core.modes import TrackerMode
from sm64_events.library.adoptions import DEFAULT_STRATEGY, sheet_strategy
from sm64_events.library.audit import row_key
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


def _blocks(targets) -> list:
    """The payload's targets, grouped one block per sheet heading -- a
    target plus any `split_from` targets carved out of it (`build.py::
    _apply_splits`), which have no opening row of their own."""
    grouped = []
    for target in targets:
        if target.get("split_from") and grouped:
            grouped[-1].append(target)
        else:
            grouped.append([target])
    return grouped


def _find_item(row, block):
    """`(target, item)` for the payload approach/subsection this row's own
    worksheet text names, searched across the block's targets -- an approach
    by its version-stripped name (the payload merges a (JP)/(US) pair under
    one), a subsection by its raw one (the payload never strips those).

    Searching the whole block rather than one target is what puts a carved-out
    star's rows on that star: the split partitions approaches and subsections
    between parent and carve-out, so a name lives in exactly one of them.

    A name is not unique inside a block -- 49 live rows share one with a
    sibling ("Warp fadeout" once per route, "100 coin star Xcam" once per
    100-coin route), so the row's own ids break the tie, and they break all
    49. Ids only DECIDE between same-name candidates: a lone candidate wins
    on its name, because a merged (JP)/(US) approach carries the UNION of its
    two rows' ids and would lose an equality test against either one."""
    if not block:
        return None, None
    collection = "approaches" if row.kind == "approach" else "subsections"
    name = base_name(row.label) if row.kind == "approach" else row.label
    named = [(target, item) for target in block
             for item in (target.get(collection) or [])
             if item.get("name") == name]
    if not named:
        return None, None
    for target, item in named:
        if set(item.get("ids") or ()) == set(row.ids):
            return target, item
    return named[0]


def names_the_thing(target, item, kind: str) -> bool:
    """Does this row name the THING, rather than a way of doing it?

    The sheet gives a star one row carrying its own name ("Chip off Whomp's
    Block") and then a row per named strategy ("Triple jump strat"); a
    subsection row names the piece being practised. Those first rows are
    about the star or the piece, so what belongs in them is your best time on
    it, however you got it -- while a strategy row means only times set THAT
    way.

    The predicate is `adoptions.sheet_strategy`'s, not a second copy of it:
    that function already answers exactly this question for the IMPORT
    direction (it files a target-named row and every subsection under the
    default strategy, "a piece's community timing is its Standard"), and the
    two doors are required to agree about which rows are which. Measured on
    the live sheet 2026-09-02: 117 of 118 star targets carry exactly one
    approach whose name IS the target's own label, and none carries two, so
    the test is both available and unambiguous. The one exception is "Slide
    Star (Under 21 Seconds)", whose rows are "Under 21" and "Late wall bounce
    strat (U21)" -- it keeps the strategy match and exports blank unless a
    name lines up. A 100-coin route's own row is NOT the thing either --
    four routes share one entity, so its slot is the route's label (round
    28: the export used to read the older two-argument rule, fell back
    blind on such a row, and printed a sibling route's time on it)."""
    if not target:
        return False
    return sheet_strategy(target, item, kind) == DEFAULT_STRATEGY


def _claimed_names(block) -> dict:
    """{id(item): strategy name} for every approach in the block -- what
    each row CLAIMS, so a row that names the thing can ask for whatever is
    left over (`_ask`)."""
    return {id(item): sheet_strategy(target, item, "approach")
            for target in (block or [])
            for item in (target.get("approaches") or [])}


def _as_time(answer):
    """`resolve`'s answer as `(cs, platform)`: a bare centisecond count (every
    test double, and any caller that has no platform to offer) is a time with
    no stamp; a `(cs, platform)` pair is taken as is. None stays None."""
    if answer is None:
        return None
    if isinstance(answer, tuple):
        cs, platform = answer
        return None if cs is None else (cs, platform)
    return (answer, None)


def _cell_for(row, block, resolve, place, claimed=None, held=None) -> dict:
    """One worksheet cell: `{"text", "platform"}` -- the sheet's own time
    notation (or `""`), and the machine the answering PB says set it
    (`"emu"` / `"n64"` / None for unstamped or empty). Round 29 item 2: the
    clipboard's HTML half colours a cell by this, the way Raisn's column
    colours his."""
    target, item = _find_item(row, block)
    if item is None:
        return {"text": "", "platform": None}
    # The import's own order, and its own strategy name -- see the module
    # docstring. `place` outranks the star fallback because a row he has
    # explicitly linked to a piece he built is about that piece.
    #
    # ROUND 25: a row that NAMES THE THING asks with no strategy at all.
    # Until now every row asked for the SHEET's own name, which closes the
    # round trip for a column he IMPORTED and misses almost everything he
    # PLAYED -- his times are filed under the names HE picked. Measured
    # through this door on his live database (268 PBs): 399 asks, 3 answers,
    # because it was asking for "Chip off Whomp's Block" where he had
    # "Standard" and "Wall Kicks Will Work" where he had "Backflip WK". A
    # strategy-blind ask on a star's own row is the same number his Scorecard
    # already shows for that star, so those two surfaces cannot disagree
    # either. Same run after the change: 42 answers.
    blind = names_the_thing(target, item, row.kind)
    own_strategy = sheet_strategy(target, item, row.kind)
    # What the block's OTHER rows claim. A row that names the thing may
    # carry only what none of them does -- its own name is not "another
    # row", and neither is its (JP)/(US) twin, which is the same item.
    claimed = claimed or _claimed_names(block)
    others = frozenset(name for key, name in claimed.items()
                       if key != id(item))
    placed = place(target, item, row.kind) if place is not None else None
    time = None
    if placed:
        placed_entity, timer_mode, strategy = placed
        time = _ask(resolve, placed_entity, strategy or own_strategy,
                    timer_mode, row.version, blind, others)
    elif (row.kind == "approach"
            and (target.get("entity_key") or "").startswith("star:")):
        time = _ask(resolve, target["entity_key"], own_strategy, "igt",
                    row.version, blind, others)
    if time is None and held is not None:
        # Nothing here answers -- the row has no home, or a home with no
        # time in it -- so the cell an import HELD for it prints back as
        # written (round 28). A held cell is released the moment its row
        # lands, so this can never print over a real personal best. A held
        # cell is not an attempt, so its platform rides the hold itself
        # (round 29 item 2) -- `held` may answer `(cs, platform)` or a bare
        # cs, like `resolve`.
        time = _as_time(held(row_key(target, item.get("name") or "",
                                     item.get("ids") or ()), row.version))
    if time is None:
        return {"text": "", "platform": None}
    cs, platform = time
    return {"text": sheet_time(cs), "platform": platform}


def _line_for(row, block, resolve, place, claimed=None, held=None) -> str:
    return _cell_for(row, block, resolve, place, claimed, held)["text"]


def _ask(resolve, entity_key, strategy, timer_mode, version, blind,
         others=frozenset()):
    """The row's own strategy FIRST, then -- only on a row that names the
    thing -- your best on it however you set it, among the times NO OTHER
    ROW of the block claims.

    The order is what lets one door serve two things he asked for a round
    apart, and it took a measurement to see they were not in conflict. Round
    25: a column he PLAYED came back nearly empty, because his times are
    filed under the names HE picked and the export asked for the sheet's, so
    a star's own row began asking with no strategy at all. Round 27: a column
    he IMPORTED must export back IDENTICALLY -- and the import files a star
    row's time under that row's OWN name, so a blind ask answered with his
    fastest time on the star from some OTHER row and the round trip broke on
    104 of one runner's rows.

    Asking by name first settles both. An imported column has a PB under the
    exact name, so it round-trips; a played one has none there, the name
    misses, and the blind fallback still carries it. A row that names a
    STRATEGY never falls back at all -- it means only times set that way.

    Round 28: the blind ask carries `others` -- the strategy names every
    OTHER row of the block files under -- and the caller answers with the
    fastest time filed under none of them. Without it the star's own row
    printed a SIBLING row's time (THI's Tip Top: a runner with no time on
    the plain route exported "No mountain clip" 20.90 there, and the
    ten-runner sweep's whole `extra` column was this). A time he filed
    under a name the sheet has no row for -- his own picks -- is exactly
    what is left over, so round 25's case still lands."""
    # A row that names the THING is that thing's Standard strategy (his
    # ruling, 2026-09-02: "Standard maps to any row that's just the star
    # name / segment name"), which is what `sheet_strategy` answers for it,
    # what the import files it under, and what he practises under himself
    # -- so the name-first ask on a blind row IS the Standard ask.
    time = _as_time(resolve(entity_key, strategy, timer_mode, version))
    if time is None and blind:
        time = _as_time(resolve(entity_key, None, timer_mode, version,
                                excluding=others))
    return time


def column_lines(rows, payload, resolve, place=None, held=None) -> list:
    """`column_cells`' texts alone -- one string per worksheet row."""
    return [cell["text"] for cell in column_cells(rows, payload, resolve,
                                                  place=place, held=held)]


# The sheet's own convention for a runner's column (Raisn's rows 2 and 3,
# and round 30 item 7's ask: "the first two lines should be the EMU color,
# and the N64 color. Emu is first cell, N64 is second cell. All caps"): a
# legend cell per platform, in worksheet row order. The values come from the
# platform registry, upper-cased -- never spelled here.
LEGEND_ROWS = tuple(zip((2, 3), (mode.value for mode in TrackerMode), strict=True))


def _with_legend(cells, by_row):
    """Write the legend into worksheet rows 2 and 3 -- only where no data row
    sits (the live sheet has a section header and a blank there), so a legend
    can never print over a time."""
    for worksheet_row, platform in LEGEND_ROWS:
        index = worksheet_row - 2
        if index < len(cells) and worksheet_row not in by_row and not cells[index]["text"]:
            cells[index] = {"text": platform.upper(), "platform": platform,
                            "legend": True}
    return cells


def column_cells(rows, payload, resolve, place=None, held=None,
                 legend=False) -> list:
    """One `{"text", "platform"}` per worksheet row, row 2 through the last
    data row -- `text` is the sheet's own time notation, `platform` the
    machine the answering PB says set it (`"emu"` / `"n64"`, None when the
    PB carries no stamp or the cell is empty). `text` is `""` wherever
    nothing maps: a header or spacer (no `SheetRow` at that row number at
    all), a row whose text names nothing in its own block, or one
    `resolve`/`place` refuses.

    `resolve` may answer a bare centisecond count OR a `(cs, platform)`
    pair (`_as_time`); the bare form is a time with no stamp.

    One string per worksheet row, row 2 through the last data row --
    `""` wherever nothing maps: a header or spacer (no `SheetRow` at that
    row number at all), or a row whose text names nothing in its own block,
    or one `resolve`/`place` refuses.

    Line 0 IS worksheet row 2, so pasting the column into a runner's own
    row-2 cell puts every time back on the row it came from.

    `rows` is `library/sheet.py::read_rows`'s output; `payload` is the
    library payload those same rows built (`library/build.py::build`).
    `place(target, item, kind) -> (entity_key, timer_mode, strat_tag) | None`
    is optional -- omit it (or let it answer `None`) and every row that is
    not a star approach goes blank, exactly like an import with no placer
    would drop it.

    `resolve(entity_key, strat_tag, timer_mode, version, *, excluding=())`
    answers a strategy by name, or -- `strat_tag=None` -- the fastest time
    on the entity filed under NO name in `excluding` (a time with no
    strategy at all counts as unclaimed).

    `held(row_key, version) -> (cs, platform) | cs | None` is the cell an
    import kept aside for a row with no home (round 28), printed only where
    nothing else answers; omit it and such rows stay blank.

    `legend=True` writes the two platform legend cells into worksheet rows 2
    and 3 (`LEGEND_ROWS`, marked `legend: True`) wherever those rows hold no
    data -- the column a runner keeps on the sheet opens with them."""
    if not rows:
        return []
    blocks = _blocks(payload.get("targets") or [])
    by_row = {row.row: row for row in rows}
    last_row = max(by_row)
    lines = []
    block_index = -1
    claimed = None
    for row_number in range(2, last_row + 1):
        row = by_row.get(row_number)
        if row is None:
            lines.append({"text": "", "platform": None})
            continue
        if row.opens_target:
            block_index += 1
        block = (blocks[block_index]
                 if 0 <= block_index < len(blocks) else None)
        if row.opens_target:
            claimed = _claimed_names(block)
        lines.append(_cell_for(row, block, resolve, place, claimed, held))
    return _with_legend(lines, by_row) if legend else lines

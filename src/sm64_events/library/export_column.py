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
from sm64_events.library.adoptions import DEFAULT_STRATEGY, strategy_name
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

    The predicate is `adoptions.strategy_name`'s, not a second copy of it:
    that function already answers exactly this question for the IMPORT
    direction (it files a target-named row and every subsection under the
    default strategy, "a piece's community timing is its Standard"), and the
    two doors are required to agree about which rows are which. Measured on
    the live sheet 2026-09-02: 117 of 118 star targets carry exactly one
    approach whose name IS the target's own label, and none carries two, so
    the test is both available and unambiguous. The one exception is "Slide
    Star (Under 21 Seconds)", whose rows are "Under 21" and "Late wall bounce
    strat (U21)" -- it keeps the strategy match and exports blank unless a
    name lines up."""
    if not target:
        return False
    return strategy_name(target.get("label") or "", item.get("name") or "",
                         kind=kind) == DEFAULT_STRATEGY


def _line_for(row, block, resolve, place) -> str:
    target, item = _find_item(row, block)
    if item is None:
        return ""
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
    sheet_strategy = item.get("matched_strategy") or item.get("name")
    placed = place(target, item, row.kind) if place is not None else None
    if placed:
        placed_entity, timer_mode, strategy = placed
        cs = _ask(resolve, placed_entity, strategy or sheet_strategy,
                  timer_mode, row.version, blind)
    elif (row.kind == "approach"
            and (target.get("entity_key") or "").startswith("star:")):
        cs = _ask(resolve, target["entity_key"], sheet_strategy, "igt",
                  row.version, blind)
    else:
        return ""
    return sheet_time(cs) if cs is not None else ""


def _ask(resolve, entity_key, strategy, timer_mode, version, blind):
    """The row's own strategy FIRST, then -- only on a row that names the
    thing -- your best on it however you set it.

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
    STRATEGY never falls back at all -- it means only times set that way."""
    cs = resolve(entity_key, strategy, timer_mode, version)
    if cs is None and blind:
        cs = resolve(entity_key, None, timer_mode, version)
    return cs


def column_lines(rows, payload, resolve, place=None) -> list:
    """One string per worksheet row, row 2 through the last data row --
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
    would drop it."""
    if not rows:
        return []
    blocks = _blocks(payload.get("targets") or [])
    by_row = {row.row: row for row in rows}
    last_row = max(by_row)
    lines = []
    block_index = -1
    for row_number in range(2, last_row + 1):
        row = by_row.get(row_number)
        if row is None:
            lines.append("")
            continue
        if row.opens_target:
            block_index += 1
        block = (blocks[block_index]
                 if 0 <= block_index < len(blocks) else None)
        lines.append(_line_for(row, block, resolve, place))
    return lines

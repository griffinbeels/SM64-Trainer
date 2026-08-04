"""The Ultimate Sheet's row grammar, and the one classification that must not
go wrong.

Some rows time a WHOLE target and some time only a stretch inside it. Reading
`[1|2] Warp fadeout` (15.90) as an approach would publish a 15.90s way of
doing a star whose real times are 43s, and that error flows into the fitted
ladders, the Library and every adoption. Three signals answer it and only one
is sound in both directions (spec 2026-08-04-ultimate-sheet-library):

  * already-seen ids -> subsection, DEFINITIVELY. The converse does NOT
    follow: a new id does not prove a whole-target time. Today's sheet holds
    no new-id subsection, which is not the same as the rule being sound.
  * grey column-A font (FF434343) -> subsection. Styling, so one vote only --
    a maintainer can restyle without telling anyone.
  * a subsection is materially faster than the approaches it belongs to.
    Measured scoped to the row's OWN ids: 144/145, median ratio 0.507.

The temporal signal is a VETO rather than a classifier, and the measurement is
why. Setting stage-RTA rows aside, the slowest real approach row sits at 0.770
of its basis while subsections run to 0.940 -- so "faster than its basis" alone
would flag 8 legitimate approaches, but "faster than 0.70 of its basis" flags
NONE of the 268 real approach rows and still catches 77% of the subsections.
That is the threshold below: a whole-target time cannot be a third faster than
the target's own best, and a part cannot be slower than the whole.

Stage RTA rows are exempt, and that exemption is load-bearing rather than
tidy: a "70 star" route is legitimately a third the length of the full-stage
route beside it (THI 96.40 against 314.74), which is a different ROUTE and not
a subsection. Those six rows are the entire reason a naive ratio test fails.

When the ids do not settle it and the other two disagree, we RAISE naming the
row rather than pick a winner.

Column A's BOLD marks where a new target begins. Its FILL does not: the fill
alternates between two greens as banding and only 38 of the 252 target rows
carry a header fill, so reading the boundary off the fill is wrong in a way
that looks right.
"""
import re
from dataclasses import dataclass, field

from sm64_events.library.workbook import SHEET_MAIN, read_sheet

GREY_FONT = "FF434343"
# 9:59.96 is verbatim the "over 1 minute" format example on the sheet's own
# rules tab, and one live row carries it against a 66.9s target. Refusing it
# is the same instinct as the poller's implausible-read refusal.
PLACEHOLDER_CS = 59996

FIRST_RUNNER_COL = 7          # A-E are script-owned, F is a separator

# A whole-target time below this fraction of its basis cannot be one. Measured
# over the live sheet: 268 non-RTA approach rows bottom out at 0.770, 145
# subsections reach 0.940 with a median of 0.507. 0.70 sits in the gap.
SUBSECTION_VETO_RATIO = 0.70

_ROW = re.compile(r"^\s*\[([\d|]+)\]\s*(.+?)\s*$")
_TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{1,3})\.(\d{2})$")
_VERSION = re.compile(r"\((JP|US)\)\s*$", re.I)
# A stage RTA times a whole course rather than a target, and its family holds
# routes of genuinely different lengths (a 70-star route against a full one).
# The ratio veto cannot speak about those.
_STAGE_RTA = re.compile(r"\bRTA\b")


class ClassificationConflict(Exception):
    """The font and the temporal signal disagree about one row."""


@dataclass
class SheetRow:
    row: int
    section: str
    label: str
    ids: frozenset
    kind: str
    opens_target: bool
    version: str | None
    best_cs: int | None
    best_runner: str
    ideal_cs: int | None
    fill_rate: float | None
    entries: dict = field(default_factory=dict)


def parse_time(text: str) -> int | None:
    """'12.60' / '1:20.63' -> centiseconds. None when unparseable, and None
    for the sheet's own format placeholder, which is a typo rather than data."""
    match = _TIME.match((text or "").strip())
    if not match:
        return None
    centiseconds = (int(match.group(1) or 0) * 6000
                    + int(match.group(2)) * 100 + int(match.group(3)))
    return None if centiseconds >= PLACEHOLDER_CS else centiseconds


def _fill_rate(text: str) -> float | None:
    text = (text or "").strip().rstrip("%")
    try:
        return round(float(text) / 100, 6)
    except ValueError:
        return None


def runner_columns(cells) -> dict:
    """{column index: runner name} from the header row."""
    return {col: cell.value.strip()
            for (row, col), cell in cells.items()
            if row == 1 and col >= FIRST_RUNNER_COL and cell.value.strip()}


def _classify(ids, seen, grey, best_cs, basis_cs, exempt, label, row) -> str:
    """One row's kind. `basis_cs` is the best of the approaches this row's own
    ids name, falling back to the target's best approach so far when the row
    introduces an id nobody has timed yet."""
    if ids <= seen:
        return "subsection"                          # definitive, one-way
    kind = "subsection" if grey else "approach"
    if exempt or best_cs is None or basis_cs is None:
        return kind                                  # the veto cannot speak
    ratio = best_cs / basis_cs
    if kind == "approach" and ratio < SUBSECTION_VETO_RATIO:
        raise ClassificationConflict(
            f"row {row} {label!r}: the font calls this a whole-target time, "
            f"but {best_cs} cs is {ratio:.2f} of its basis {basis_cs} cs -- "
            f"below the {SUBSECTION_VETO_RATIO} floor no real approach reaches")
    if kind == "subsection" and ratio > 1.0:
        raise ClassificationConflict(
            f"row {row} {label!r}: the font calls this a subsection, but "
            f"{best_cs} cs is slower than its basis {basis_cs} cs -- a part "
            f"cannot be slower than the whole")
    return kind


def read_rows(data: bytes) -> list:
    """Every bracketed data row of the main tab, classified and timed."""
    cells = read_sheet(data, SHEET_MAIN)
    runners = runner_columns(cells)
    last_row = max(row for row, _ in cells)
    out, section, seen, best_by_id = [], "", set(), {}
    target_best, target_label = None, ""

    def text(row, col):
        cell = cells.get((row, col))
        return cell.value if cell else ""

    for row in range(2, last_row + 1):
        head = cells.get((row, 1))
        if head is None or not head.value.strip():
            continue
        match = _ROW.match(head.value)
        if not match:
            section, seen, best_by_id = head.value.strip(), set(), {}
            target_best, target_label = None, ""
            continue
        ids = frozenset(match.group(1).split("|"))
        label = match.group(2)
        best_cs = parse_time(text(row, 2))
        if head.bold:
            seen, best_by_id = set(), {}
            target_best, target_label = None, label
        own = [best_by_id[i] for i in ids if best_by_id.get(i) is not None]
        kind = _classify(ids, seen, head.font_rgb == GREY_FONT, best_cs,
                         min(own) if own else target_best,
                         bool(_STAGE_RTA.search(target_label)), label, row)

        entries = {}
        for col, runner in runners.items():
            cell = cells.get((row, col))
            if cell is None:
                continue
            centiseconds = parse_time(cell.value)
            if centiseconds is not None:
                entries[runner] = (centiseconds, cell.link)

        version = _VERSION.search(label)
        out.append(SheetRow(
            row=row, section=section, label=label, ids=ids, kind=kind,
            opens_target=bool(head.bold),
            version=version.group(1).lower() if version else None,
            best_cs=best_cs, best_runner=text(row, 3).strip(),
            ideal_cs=parse_time(text(row, 4)),
            fill_rate=_fill_rate(text(row, 5)), entries=entries))

        if kind == "approach" and best_cs is not None:
            for one in ids:
                best_by_id[one] = best_cs
            target_best = (best_cs if target_best is None
                           else min(target_best, best_cs))
        seen |= ids
    return out

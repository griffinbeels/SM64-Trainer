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

A new target begins where the id lineage RESTARTS -- a non-grey row that either
opens a section or re-introduces `[1]` on its own after 1 has been used. Bold
used to be the boundary here and is now only a cross-check, because the sheet
moved under us: between 2026-08-04T22:14 and 2026-08-05T09:15 someone unbolded
all nine of Cool Cool Mountain's target rows, with every row, every id and all
147 grey fonts untouched. Bold-as-authority silently lost nine targets and
their approaches; the structural rule reproduces all 252 on BOTH revisions,
agreeing with bold exactly on the day bold was still complete.

Column A's FILL was never the boundary either: it alternates between two greens
as banding and only 38 of the 252 target rows carry a header fill, so reading
the boundary off the fill is wrong in a way that looks right.
"""
from sm64_events.core.modes import TrackerMode
import re
from dataclasses import dataclass, field

from sm64_events.library.workbook import SHEET_MAIN, read_sheet

GREY_FONT = "FF434343"
# 9:59.96 is verbatim the "over 1 minute" format example on the sheet's own
# rules tab, and one live row carries it against a 66.9s target. Refusing it
# is the same instinct as the poller's implausible-read refusal.
PLACEHOLDER_CS = 59996

FIRST_RUNNER_COL = 7          # A-E are script-owned, F is a separator
# Sub-headers inside a group: "★ BoB" sits under "Castle Movements (Lobby)".
SUBHEADER_MARK = "★"

# A whole-target time below this fraction of its basis cannot be one. Measured
# over the live sheet: 268 non-RTA approach rows bottom out at 0.770, 145
# subsections reach 0.940 with a median of 0.507. 0.70 sits in the gap.
SUBSECTION_VETO_RATIO = 0.70

# "100 coin star Xcam", "Red coin star Xcam": the x-cam of a STAR GRAB, which
# is a whole-target time for the star the target maps to -- not a part of it.
# These carry the union of several approaches' ids ("[1|2]"), so the id rule
# calls them subsections, and 25 of them were wrong until the human corrected
# every one by hand (2026-08-05). The pattern matches those 25 exactly and
# nothing else: every OTHER Xcam row on the sheet times a door, a text box or
# an entry ("Whomp text Xcam", "Attic door Xcam", "Cannon entry Xcam"), which
# genuinely are parts. Named vocabulary rather than styling, so unlike bold it
# cannot drift without the row changing meaning.
_STAR_XCAM = re.compile(r"\bstar Xcam\b", re.I)
_ROW = re.compile(r"^\s*\[([\d|]+)\]\s*(.+?)\s*$")
_TIME = re.compile(r"^(?:(\d{1,2}):)?(\d{1,3})\.(\d{2})$")
# The ROM version sits in the trailing parenthetical, but it is not always
# ALONE there: 163 rows write a bare "(JP)"/"(US)", and 58 write it as one
# comma-separated token beside other words -- "(Toad, US)", "(☆15 MIPS Clip,
# JP)", "(120 star file, US)", "(JP, 34c -)". Anchoring on the bare form read
# all 58 as version-less, which cost three things at once: their JP and US
# halves never paired into one approach, their entries carried no version so
# the target page's mode toggle never rendered, and 14 targets went into the
# browse grid wearing their JP row's name with nothing saying a US row existed
# at all. Live report 2026-08-09: "I don't see the HMC Door Mips Clip US
# version in our library" -- it was there, one click in, under a JP name.
_TRAILING_PAREN = re.compile(r"\(([^()]*)\)\s*$")
_VERSION_TOKEN = re.compile(r"^(JP|US)$", re.I)
# A stage RTA times a whole course rather than a target, and its family holds
# routes of genuinely different lengths (a 70-star route against a full one).
# The ratio veto cannot speak about those.
_STAGE_RTA = re.compile(r"\bRTA\b")


def entry_version(entry: dict) -> str | None:
    """The ROM one sheet ENTRY belongs to, or `None` meaning BOTH.

    THE door for that question -- the import stamps a time with it and the
    Scorecard's runner goal offers a time by it, so the two can never
    disagree about which ROM a time is.

    An entry's version is the one its own worksheet row's name declares
    (`version_of`), and nothing else. His ruling, round 35 (2026-09-05):
    *"if a row doesn't include a distinction between US/JP, we should assume
    that it's the same for both, so we should have an entry for both US and
    JP being the same."*

    In particular NOT the target's, which is where this went wrong: the
    payload merges a "(JP)"/"(US)" pair into ONE target, and that target
    keeps only one of the two labels -- so 3,123 entries on 68 rows that
    declare no version of their own (Big Bob-omb's "Warp fadeout" under a
    target stamped `jp`, and 67 more) were landing as times on a ROM the
    sheet never claimed for them. The whole target is really both."""
    return entry.get("version") or None


def version_of(label: str) -> str | None:
    """The ROM version a row's name declares, or `None` where it declares none.

    Only the TRAILING parenthetical is read, and only a whole comma-separated
    token counts -- so "Punch grab in front of LLL (MIPS, JP)" is JP while
    "US" appearing inside a word never is."""
    tail = _TRAILING_PAREN.search(label)
    if tail is None:
        return None
    for part in tail.group(1).split(","):
        if _VERSION_TOKEN.match(part.strip()):
            return part.strip().lower()
    return None


def base_name(label: str) -> str:
    """The label minus its ROM-version token -- what pairs a JP row with its US
    sibling.

    ONLY the version token goes. Every other word in the parenthetical
    distinguishes real rows, so "(Toad, JP)" and "(Toad, US)" become one
    approach while "(MIPS, JP)" and "(☆50 MIPS, US)" stay two -- those name
    genuinely different setups, not two versions of one."""
    tail = _TRAILING_PAREN.search(label)
    if tail is None or version_of(label) is None:
        return label.strip()
    kept = [part.strip() for part in tail.group(1).split(",")
            if part.strip() and not _VERSION_TOKEN.match(part.strip())]
    head = label[:tail.start()].strip()
    return f"{head} ({', '.join(kept)})" if kept else head


class ClassificationConflict(Exception):
    """The font and the temporal signal disagree about one row."""


@dataclass
class SheetRow:
    row: int
    group: str
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
    # What the sheet's own styling claims, kept only so a drift between it and
    # the structural boundary is visible instead of silent.
    bold: bool = False


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


# The legend a runner may write into rows 2 and 3 of their own column: a cell
# reading "Emu" in one fill and "N64" in another, and every time below wearing
# one of the two. Round 29 item 2 (2026-09-04), measured on the live export:
# Raisn does this (A5A9F1 for the emulator, the theme's orange for the
# console), and he is the only runner whose rows 2/3 carry a platform legend
# -- 2 of 473 columns carry any two-fill legend there, and the other's labels
# are a name and a course. So a fill means a platform ONLY where the column's
# own legend says so; with no legend, no cell is stamped, and `core/modes.py::
# platform_of` reads an absent stamp as the emulator.
LEGEND_ROWS = (2, 3)
_EMU, _N64 = TrackerMode.EMU.value, TrackerMode.N64.value
_LEGEND_LABELS = {_EMU: _EMU, "emulator": _EMU, "pc": _EMU,
                  _N64: _N64, "console": _N64}
# Raisn's own hand-picked near shades of his two fills (A1A3FF and ACABFF
# beside A5A9F1; measured 2026-09-04): a cell is stamped by the NEAREST legend
# fill within this RGB distance, and left unstamped past it.
LEGEND_FILL_TOLERANCE = 48


def _rgb_distance(a: str, b: str) -> float:
    return sum((int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) ** 2
               for i in (0, 2, 4)) ** 0.5


def runner_legends(cells, runners) -> dict:
    """{column index: {fill rgb: platform}} for every runner whose rows 2/3
    name the two platforms in two different fills; a column with no such
    legend is absent."""
    legends = {}
    for col in runners:
        fills = {}
        for row in LEGEND_ROWS:
            cell = cells.get((row, col))
            if cell is None or not cell.fill_rgb:
                continue
            platform = _LEGEND_LABELS.get(cell.value.strip().lower())
            if platform:
                fills[cell.fill_rgb] = platform
        if len(set(fills.values())) == 2:
            legends[col] = fills
    return legends


def platform_for_fill(fill_rgb, legend) -> str | None:
    """The platform a timed cell's fill names under this column's legend:
    the nearest legend fill within `LEGEND_FILL_TOLERANCE`, else None."""
    if not fill_rgb or not legend:
        return None
    nearest = min(legend, key=lambda candidate: _rgb_distance(candidate, fill_rgb))
    if _rgb_distance(nearest, fill_rgb) > LEGEND_FILL_TOLERANCE:
        return None
    return legend[nearest]


def _classify(ids, seen, grey, best_cs, basis_cs, exempt, label, row) -> str:
    """One row's kind. `basis_cs` is the best of the approaches this row's own
    ids name, falling back to the target's best approach so far when the row
    introduces an id nobody has timed yet."""
    if _STAR_XCAM.search(label):
        # A star grab IS the target, however many approaches share the row.
        return "approach"
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
    legends = runner_legends(cells, runners)
    last_row = max(row for row, _ in cells)
    # Headers nest two deep: "Castle Movements (Lobby)" then "★ BoB". Keeping
    # only the innermost loses the outer context entirely, which is what tells
    # a mapper that 113 rows are movements rather than unrecognised stars.
    out, group, section, seen, best_by_id = [], "", "", set(), {}
    target_best, target_label, in_section = None, "", False

    def text(row, col):
        cell = cells.get((row, col))
        return cell.value if cell else ""

    for row in range(2, last_row + 1):
        head = cells.get((row, 1))
        if head is None or not head.value.strip():
            continue
        match = _ROW.match(head.value)
        if not match:
            section = head.value.strip()
            if not section.startswith(SUBHEADER_MARK):
                group = section
            seen, best_by_id = set(), {}
            target_best, target_label, in_section = None, "", False
            continue
        ids = frozenset(match.group(1).split("|"))
        label = match.group(2)
        best_cs = parse_time(text(row, 2))
        grey = head.font_rgb == GREY_FONT
        # The lineage restart, not the styling. A grey row is never a boundary
        # however its ids read, which is what stops WF's `[1] Whomp text Xcam`
        # -- a subsection reusing id 1 -- from opening a target of its own.
        opens = not grey and (not in_section or (ids == {"1"} and "1" in seen))
        in_section = True
        if opens:
            seen, best_by_id = set(), {}
            target_best, target_label = None, label
        own = [best_by_id[i] for i in ids if best_by_id.get(i) is not None]
        kind = _classify(ids, seen, grey, best_cs,
                         min(own) if own else target_best,
                         bool(_STAGE_RTA.search(target_label)), label, row)

        entries = {}
        for col, runner in runners.items():
            cell = cells.get((row, col))
            if cell is None:
                continue
            centiseconds = parse_time(cell.value)
            if centiseconds is not None:
                entries[runner] = (centiseconds, cell.link,
                                   platform_for_fill(cell.fill_rgb, legends.get(col)))

        out.append(SheetRow(
            row=row, group=group, section=section, label=label, ids=ids, kind=kind,
            opens_target=opens, bold=bool(head.bold),
            version=version_of(label),
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

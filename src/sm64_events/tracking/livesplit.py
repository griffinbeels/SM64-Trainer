"""Reading a LiveSplit splits file for the times worth keeping.

A `.lss` is XML: a `<Run>` of `<Segment>`s, each with a `<Name>`, a
`<BestSegmentTime>` (the GOLD — the fastest that split has ever been) and a
`<SplitTimes>` history. The gold is what a practicer already has and this tool
did not: proof of the best they have ever done one piece of the run.

TWO THINGS MAKE THIS HONEST RATHER THAN CLEVER.

A LiveSplit split is a stretch of the run, so its gold is an RTA time and lands
on a SEGMENT — never on a star, whose personal bests are Usamune IGT. Filing a
real-time split against a star's IGT ladder would compare two different
measurements and read as a wildly good time.

And a segment is matched by NAME against segments the player built HERE, so the
id it produces is this database's own. That is the exact difference from the
Ultimate Sheet's segment rows, whose ids came from whichever machine scraped
them: a foreign id is worse than a missing one, because it very likely exists
here too and names something else.

`RealTime` is preferred over `GameTime` because a LiveSplit game-time column is
only populated by an auto-splitter, and an SM64 one measures a different clock
again. When only `GameTime` is present it is read and SAID SO, rather than
silently mixing two clocks in one import.

Stdlib only. A splits file is a document from outside, so the obvious worry is
an XML entity pointed at a local file — MEASURED rather than guarded against:
CPython's `ElementTree` does not resolve external entities at all and raises
`ParseError: undefined entity` the moment one is referenced (checked
2026-08-21). A hand-rolled parser hook on top of that would be a guard nobody
can demonstrate, which is worse than none.
"""
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

# `HH:MM:SS.fffffff`, and LiveSplit writes all of it even for a 12-second
# split. Minutes and hours are optional anyway, because hand-edited files
# exist.
_TIME = re.compile(r"""^
    (?:(?:(?P<hours>\d+):)?(?P<minutes>\d+):)?
    (?P<seconds>\d+)
    (?:\.(?P<fraction>\d+))?
$""", re.X)


@dataclass(frozen=True)
class Gold:
    """One split's best-ever time, in the file's own words."""
    name: str
    time_cs: int
    clock: str          # "real" | "game"


def parse_time_cs(text: str) -> int | None:
    """Centiseconds, ROUNDED to the nearest.

    LiveSplit stores seven fractional digits written by floating-point
    arithmetic, so a real 1:06.83 is on disk as `00:01:06.8299999`.
    TRUNCATING that gives 1:06.82 — a centisecond FASTER than the time
    actually run, which is the flattering direction and the one direction an
    import must never move a number. Rounding recovers it exactly.

    Any residual error is then absorbed conservatively downstream:
    `core/timefmt.frame_at_or_after` rounds a centisecond UP to the next frame
    the timer can display."""
    match = _TIME.match(str(text or "").strip())
    if not match:
        return None
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds"))
    whole_cs = ((hours * 60 + minutes) * 60 + seconds) * 100
    fraction = match.group("fraction") or ""
    if not fraction:
        return whole_cs or None
    # Integer arithmetic, never float: the digits are already a decimal and
    # re-floating them is how the artefact got here in the first place.
    padded = fraction.ljust(3, "0")[:3]
    total = whole_cs + (int(padded) + 5) // 10
    return total or None


def _parse(data):
    """The document.

    `ParseError` is deliberately left to reach the caller: a file that is not
    a splits file at all must SAY so, because "nothing landed" and "that was a
    screenshot" look identical from the outside and only one is worth acting
    on. Bytes are decoded with `utf-8-sig` — LiveSplit writes a BOM."""
    return ElementTree.fromstring(
        data.decode("utf-8-sig", "replace") if isinstance(data, bytes)
        else data)


def read_golds(data) -> list[Gold]:
    """Every split that carries a best-ever time, in file order.

    A split with no gold — one nobody has ever finished — is simply absent
    rather than a zero, because a zero would land as an impossibly fast time.
    """
    root = _parse(data)
    golds = []
    for segment in root.iter("Segment"):
        name = (segment.findtext("Name") or "").strip()
        best = segment.find("BestSegmentTime")
        if not name or best is None:
            continue
        for tag, clock in (("RealTime", "real"), ("GameTime", "game")):
            time_cs = parse_time_cs(best.findtext(tag) or "")
            if time_cs:
                golds.append(Gold(name=name, time_cs=time_cs, clock=clock))
                break
    return golds


def candidates_for(data, catalog, strategy: str | None = None):
    """`([ImportCandidate, ...], [Unresolved, ...])` for a splits file.

    `catalog` must carry the player's own segment names
    (`import_names.segment_catalog`); a split naming anything else — a star, a
    stage, "Reset" — is reported rather than dropped, because a file of thirty
    splits that lands four times has twenty-six answers the player is owed.
    """
    from sm64_events.tracking.import_names import Unresolved, resolve_target
    from sm64_events.tracking.importing import ImportCandidate

    candidates, unresolved = [], []
    for number, gold in enumerate(read_golds(data), start=1):
        entity_key = resolve_target(gold.name, catalog)
        if not entity_key:
            unresolved.append(Unresolved(number, gold.name, "unknown_target"))
            continue
        if not entity_key.startswith("segment:"):
            # A LiveSplit gold is a REAL-TIME stretch of the run. Landing one
            # on a star would file it against an IGT ladder and read as a
            # wildly good time.
            unresolved.append(Unresolved(number, gold.name, "not_a_segment"))
            continue
        candidates.append(ImportCandidate(
            entity_key=entity_key, strat_tag=strategy or "",
            time_cs=gold.time_cs, timer_mode="rta"))
    return candidates, unresolved

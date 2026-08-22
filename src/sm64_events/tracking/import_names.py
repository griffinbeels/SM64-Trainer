"""Turning typed text into a target and a time.

The sheet door works because the Ultimate Sheet already speaks our vocabulary.
Every OTHER source — a pasted block, a LiveSplit file, somebody's own xcam
spreadsheet — arrives as names a person wrote, so something has to answer
"which star is `BoB 1`?" before `tracking/importing.py` can decide anything.

THE FORMAT IS THE COMMUNITY'S, NOT A NEW ONE. The names this answers to are the
ones already in play: the game's own star names, the abbreviations every runner
types (`BoB 1`, `WF 100c`), the Ultimate Sheet's target labels, and — for a
LiveSplit file — the names of the segments the player built here. Inventing a
new identifier scheme would mean everyone reformatting data they already have,
which is the opposite of the point.

Pure: no database, no I/O. The caller assembles a `Catalog` from whatever
sources it has and this reads it.

WHAT IT REFUSES IS THE DELIVERABLE. A resolver that silently drops the lines it
did not understand reports a clean import of half the data, and the half that
vanished is invisible. Every line that fails comes back with its text, its line
number and the reason — the same discipline `tools/scrape_sheet.py` states
about its own "unknown:" list.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from sm64_events.memory.addresses import (COURSE_ABBREV, COURSE_NAMES,
                                          STAR_NAMES)
from sm64_events.tracking.importing import ImportCandidate

# The 100-coin star's slot. Named here rather than repeated as a literal in the
# three places below that reach for it.
HUNDRED_COIN_SLOT = 6

# Fields inside one line. A spreadsheet paste is TAB separated, a hand-typed
# block is usually aligned with runs of spaces, and a CSV paste is commas —
# all three read the same way, so nobody has to reformat what they already
# have. A single space is NOT a separator: star names contain them.
_FIELDS = re.compile(r"\t+|\s{2,}|\s*[,|;]\s*")

# `M:SS.CC`, `SS.CC`, and our own `M'SS"CC` — plus the loose forms people
# actually type (`1:19.3`, `23`). Centiseconds are padded, never truncated:
# `.3` is three tenths.
_TIME = re.compile(r"""^
    (?:(?P<minutes>\d{1,3})\s*[:'’]\s*)?      # optional minutes
    (?P<seconds>\d{1,3})
    (?:\s*[.\"”]\s*(?P<centis>\d{1,2}))?      # optional centiseconds
$""", re.X)

# "BoB 1", "Whomp's Fortress 5", "WF 100c", "THI 100". The trailing token is
# the star as PLAYERS count them — 1-indexed — or the 100-coin star.
_COURSE_AND_SLOT = re.compile(r"^(?P<course>.+?)\s+(?P<slot>\d{1,3}\s*c?)$")


def normalise(text: str) -> str:
    """The comparison form: case-folded, accent-stripped, punctuation-free.

    `Whomp's Fortress`, `whomps fortress` and `WHOMPS  FORTRESS` are one name.
    Apostrophes are the reason this exists at all — half the star names carry
    one and nobody types the curly form."""
    folded = unicodedata.normalize("NFKD", str(text or "")).casefold()
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    # An apostrophe is DELETED and every other mark becomes a space. Both
    # halves matter: `Whomp's` has to read as `whomps`, not `whomp s`, while
    # `Bob-omb` and `Tiny-Huge` have to read as two words so the hyphen is
    # optional to type.
    return " ".join(re.sub(r"[^\w\s]", " ",
                           re.sub(r"['’`´]", "", stripped)).split())


def parse_time_cs(text: str) -> int | None:
    """Centiseconds, or None when this is not a time at all.

    None is the signal the line parser uses to find WHICH field is the time,
    so it must never guess: a star name that happens to be numeric would
    otherwise eat the field."""
    match = _TIME.match(str(text or "").strip())
    if not match:
        return None
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds"))
    centis_text = match.group("centis") or "0"
    centis = int(centis_text.ljust(2, "0"))
    if minutes and seconds >= 60:
        return None                      # "2:75.00" is not a time anyone ran
    total = (minutes * 60 + seconds) * 100 + centis
    return total or None                 # a bare 0 is not a time either


@dataclass
class Catalog:
    """Every name this import will answer to.

    `targets` maps a normalised name to an entity key; `strategies` maps an
    entity key to the strategy names known for it, so a strategy written on a
    line can be matched case-insensitively against what already exists rather
    than minting a near-duplicate of it."""
    targets: dict = field(default_factory=dict)
    strategies: dict = field(default_factory=dict)

    def add_target(self, name: str, entity_key: str) -> None:
        """First writer wins. Order therefore encodes PRECEDENCE, and the
        callers below rely on it: the game's own star names are added before
        the sheet's labels, so a sheet label that collides with a star name
        cannot redirect it."""
        key = normalise(name)
        if key and key not in self.targets:
            self.targets[key] = entity_key

    def add_strategy(self, entity_key: str, name: str) -> None:
        if name:
            self.strategies.setdefault(entity_key, {})[normalise(name)] = name

    def strategy_for(self, entity_key: str, text: str) -> str | None:
        """The known strategy this text names, or the text itself.

        Returning the raw text for an unknown name is deliberate: a strategy
        the player invented is a real strategy, and refusing it would make the
        import lossy over exactly the times they care most about."""
        text = (text or "").strip()
        if not text:
            return None
        return self.strategies.get(entity_key, {}).get(normalise(text), text)


def star_catalog(catalog: Catalog | None = None) -> Catalog:
    """The game's own vocabulary: every star name, plus the `<course> <slot>`
    shorthand every runner types.

    Added FIRST by every caller, so nothing later can redirect a real star
    name onto something else."""
    catalog = catalog or Catalog()
    for course_id, names in STAR_NAMES.items():
        for star_id, name in enumerate(names):
            catalog.add_target(name, f"star:{course_id}:{star_id}")
    for course_id, course in COURSE_NAMES.items():
        forms = [course]
        if course_id in COURSE_ABBREV:
            forms.append(COURSE_ABBREV[course_id])
        for form in forms:
            for star_id in range(6):
                # 1-indexed, because that is how they are spoken and written
                # everywhere outside our own storage.
                catalog.add_target(f"{form} {star_id + 1}",
                                   f"star:{course_id}:{star_id}")
            for hundred in ("100", "100c", "100 coins", "100 coin"):
                catalog.add_target(f"{form} {hundred}",
                                   f"star:{course_id}:{HUNDRED_COIN_SLOT}")
    return catalog


def sheet_catalog(payload: dict, catalog: Catalog | None = None) -> Catalog:
    """The Ultimate Sheet's target labels and approach names.

    This is what makes a block copied straight out of the sheet resolve with
    no editing — the labels ARE the community's names for these runs, and the
    approach names are the strategies our own ladders already use."""
    catalog = catalog or Catalog()
    for target in payload.get("targets") or []:
        entity_key = target.get("entity_key")
        if not entity_key:
            continue
        catalog.add_target(target.get("label") or "", entity_key)
        for approach in target.get("approaches") or []:
            catalog.add_strategy(
                entity_key,
                approach.get("matched_strategy") or approach.get("name") or "")
    return catalog


def segment_catalog(rows, catalog: Catalog | None = None) -> Catalog:
    """The player's OWN segment definitions, by name.

    Segment ids are local to one database, which is why the sheet's segment
    rows are dropped — but these ids came from THIS database, so they mean
    exactly what they say. That is the whole difference, and it is what lets a
    LiveSplit file land its golds on movements the player actually built."""
    catalog = catalog or Catalog()
    for row in rows or []:
        name = row.get("name") if isinstance(row, dict) else getattr(row, "name", "")
        row_id = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
        if name and row_id is not None:
            catalog.add_target(name, f"segment:{row_id}")
    return catalog


@dataclass(frozen=True)
class Unresolved:
    """A line the import could not use, in the words it was written in."""
    line: int
    text: str
    reason: str          # "no_time" | "unknown_target" | "no_target"


def resolve_target(text: str, catalog: Catalog) -> str | None:
    """The entity key this text names, or None.

    Tries the whole phrase first, then the `<course> <slot>` shape — so
    `Blast Away the Wall` wins as itself and `WF 6` resolves through the
    shorthand, without either having to know about the other."""
    whole = catalog.targets.get(normalise(text))
    if whole:
        return whole
    match = _COURSE_AND_SLOT.match(str(text or "").strip())
    if not match:
        return None
    slot = match.group("slot").replace(" ", "")
    return catalog.targets.get(normalise(f"{match.group('course')} {slot}"))


def parse_block(text: str, catalog: Catalog, timer_mode_for=None):
    """`([ImportCandidate, ...], [Unresolved, ...])` for a pasted block.

    One time per line. The TIME is found by parsing fields rather than by
    counting them, so `BoB 1  0:23.57  Standard` and `Blast Away the Wall,
    8.86` both work with no format flag: everything before the time names the
    target, everything after it names the strategy.

    Blank lines and `#` comments are skipped silently — they are punctuation,
    not data. Everything else that fails comes back in the second list.
    """
    candidates, unresolved = [], []
    for number, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = [part.strip() for part in _FIELDS.split(line) if part.strip()]
        if len(fields) == 1:
            # A single field can still be "<target> <time>" with one space
            # between them, which is how most people type one line.
            fields = _split_trailing_time(fields[0]) or fields
        times = [(index, parse_time_cs(part))
                 for index, part in enumerate(fields)]
        timed = [(index, cs) for index, cs in times if cs is not None]
        if not timed:
            unresolved.append(Unresolved(number, line, "no_time"))
            continue
        # The LAST parseable field is the time. A target whose name ends in a
        # number ("BoB 1") would otherwise claim it.
        index, time_cs = timed[-1]
        target_text = " ".join(fields[:index]).strip()
        strat_text = " ".join(fields[index + 1:]).strip()
        if not target_text:
            unresolved.append(Unresolved(number, line, "no_target"))
            continue
        entity_key = resolve_target(target_text, catalog)
        if not entity_key:
            unresolved.append(Unresolved(number, line, "unknown_target"))
            continue
        mode = timer_mode_for(entity_key) if timer_mode_for else "igt"
        candidates.append(ImportCandidate(
            entity_key=entity_key,
            strat_tag=catalog.strategy_for(entity_key, strat_text) or "",
            time_cs=time_cs, timer_mode=mode))
    return candidates, unresolved


def _split_trailing_time(text: str):
    """`["BoB 1", "0:23.57"]` for a line written with single spaces.

    Walks in from the RIGHT and takes the SHORTEST trailing run of tokens
    that parses as one time, so `BoB 1 0:23.57` splits after the `1` — the
    `1` stays with the name instead of being read as minutes."""
    tokens = text.split()
    for start in range(len(tokens) - 1, 0, -1):
        if parse_time_cs(" ".join(tokens[start:])) is not None:
            return [" ".join(tokens[:start]), " ".join(tokens[start:])]
    return None

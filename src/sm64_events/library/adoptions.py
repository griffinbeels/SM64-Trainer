"""Assigning a library row to a segment the user built.

A star approach adopts itself: the sheet row already names the star, so
`library/adopt.py` mints those at scrape time and they ship. A movement cannot.
The sheet's Castle Movements are micro-optimisations at a granularity we do not
model — 113 rows against our 63 segments — and its subsections are stretches
inside a star that no segment exists for at all. So the user builds the segment
first and then assigns the row to it, rather than us inventing 113 segments
nobody asked for (user's ruling, 2026-08-05).

That makes an assignment a USER's fact, not a community one: it lives in their
data directory beside their own settings, keyed by the row's stable name so a
sheet refresh keeps it. `library/library_overrides.json` is the other thing and
must not be confused with this — those are corrections to our READING of the
sheet, they are committed, and they are the same for everybody."""
import json
import logging
import re
from pathlib import Path

_log = logging.getLogger("sm64.library")

# What a strategy is called once adopted. A movement target usually holds one
# approach named after the target itself ("Lobby door (L) - BoB door"), and
# filing that under a segment of the same name reads as a stutter.
DEFAULT_STRATEGY = "Standard"


def load(path) -> dict:
    """{row key: entity key} — empty when absent or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {key: value for key, value in rows.items()
            if isinstance(key, str) and isinstance(value, str) and value}


def save(path, rows: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "rows": dict(sorted(rows.items()))},
                               indent=1, ensure_ascii=False),
                    encoding="utf-8", newline="")


def strategy_name(target_label: str, row_name: str, *, kind: str = "approach") -> str:
    """The strategy identity one adopted Library row contributes.

    An approach names a distinct way to complete its target, except for the
    common target-named row whose name would merely stutter. A subsection is
    different: the row names the *piece being practised*, not a way to perform
    that piece, so its community timing is the piece's Standard strategy.
    """
    return (DEFAULT_STRATEGY
            if kind == "subsection" or row_name.strip() == target_label.strip()
            else row_name)


def _normalized(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def auto_match(target_label: str, segments) -> dict | None:
    """The segment a movement target associates with UNASKED — round 6:
    "we should autoassign any segments that exist already."

    Normalized NAME equality (case and punctuation blind), nothing fuzzier,
    and the choice is measured rather than preferred: ladder proximity (the
    star matcher) is structurally unable here — the corpus segments'
    hand-seeded 3-tier Standard rows never reach `_distance`'s 4-shared-tier
    floor, and scoring all 184 entity-less laddered rows against all 18
    vetted segment strategies paired ZERO — while name equality pairs
    exactly the set a human would (one target on today's snapshot, Lakitu
    skip). `segments` is (id, name) pairs from the LIVE definition list, so
    a segment built tomorrow with a movement's name pairs on the next page
    load. An explicit assignment (a stored adoption) always outranks this."""
    wanted = _normalized(target_label)
    if not wanted:
        return None
    for segment_id, segment_name in segments:
        if _normalized(segment_name) == wanted:
            return {"entity": f"segment:{segment_id}", "name": segment_name}
    return None


def _rows(payload):
    from sm64_events.library.audit import row_key
    for target in payload["targets"]:
        for collection, kind in (("approaches", "approach"),
                                 ("subsections", "subsection")):
            for item in target[collection]:
                yield (target, item,
                       row_key(target, item["name"], item["ids"]), kind)


def find_row(payload: dict, key: str):
    """(target, item, kind) for one row key, or three ``None`` values."""
    for target, item, candidate, kind in _rows(payload):
        if candidate == key:
            return target, item, kind
    return None, None, None


def ladders(payload: dict, rows: dict) -> dict:
    """{entity key: {strategy: ladder}} for everything the user has assigned.

    A row with no fitted ladder contributes nothing: a strategy in the picker
    that grades against nothing is worse than not offering it. Two rows
    assigned to one segment simply become two strategies on it, which is the
    point — a movement with three documented ways to do it is three strategies."""
    out = {}
    for target, item, key, kind in _rows(payload):
        entity = rows.get(key)
        if not entity or not item.get("ladder"):
            continue
        name = strategy_name(target["label"], item["name"], kind=kind)
        layers = out.setdefault(entity, {"strategies": {}, "jp_strategies": {}})
        layers["strategies"].setdefault(name, item["ladder"])
        if item.get("ladder_jp"):
            layers["jp_strategies"].setdefault(name, item["ladder_jp"])
    return out


class AdoptionError(ValueError):
    """The assignment cannot be made, and the caller needs to hear why."""


class Adoptions:
    """The user's assignments, and the one place they reach the ranker.

    Holds the wiring so the router does not: load the file, validate against
    the live library, and re-merge the resulting ladders into the standards
    store on every change. Re-merging rather than appending is what makes an
    unadopt actually remove a strategy."""

    def __init__(self, path, store, standards, qualified=()):
        self.path = Path(path)
        self.store = store               # LibraryStore
        self.standards = standards       # RankStandards
        self.qualified = set(qualified)
        self._rows = {}

    def load(self) -> None:
        self._rows = load(self.path)
        self._sync()

    def rows(self) -> dict:
        return dict(self._rows)

    def ladders(self) -> dict:
        return ladders(self.store.payload, self._rows)

    def _sync(self) -> None:
        if self.standards is not None:
            self.standards.apply_sheet_ladders(self.ladders())

    def adopt(self, key: str, entity: str) -> dict:
        target, item, name = validate(self.store.payload, key, entity,
                                      self.qualified)
        self._rows[key] = entity
        save(self.path, self._rows)
        self._sync()
        return {"adopted": True, "row_key": key, "entity_key": entity,
                "strategy": name, "ladder": item["ladder"],
                "target": target["label"]}

    def unadopt(self, key: str) -> dict:
        removed = self._rows.pop(key, None)
        save(self.path, self._rows)
        self._sync()
        return {"adopted": False, "row_key": key, "entity_key": removed}

    def linked_targets(self) -> dict:
        """{entity key: [{index, label}]} — the REVERSE of the stored rows,
        for the segment editor's "which library target points at me" view
        (round 8). Computed from APPROACH assignments only: a piece link is
        a partial fact and must not present a whole target as linked."""
        from sm64_events.library.audit import row_key as make_key
        out = {}
        for position, target in enumerate(self.store.payload["targets"]):
            entities = {self._rows[key]
                        for item in target["approaches"]
                        if (key := make_key(target, item["name"],
                                            item["ids"])) in self._rows}
            for entity in entities:
                out.setdefault(entity, []).append(
                    {"index": position, "label": target["label"]})
        return out

    def adopt_target(self, index: int, entity: str) -> dict:
        """Assign EVERY laddered approach of one target to `entity` — round
        7: "If we link a segment, then it should automatically load ALL
        strategies for that segment." One save + one sync, so a half-linked
        target is unreachable; a row with no ladder skips WITH its reason
        and never sinks the batch. Approaches only — a subsection is a PART
        and keeps its own row-level link (round 6's ruling for the match,
        applied to the assignment)."""
        targets = self.store.payload["targets"]
        target = targets[index] if 0 <= index < len(targets) else None
        if target is None:
            raise AdoptionError(f"no library target at index {index}")
        if entity in self.qualified:
            raise AdoptionError(
                f"{entity} names its strategies by exit-star variant, and the "
                f"sheet rows do not say which exit star they ran")
        from sm64_events.library.audit import row_key as make_key
        adopted, skipped = [], []
        for item in target["approaches"]:
            if not item.get("ladder"):
                samples = item.get("ladder_samples", len(item["entries"]))
                skipped.append({"name": item["name"],
                                "reason": f"only {samples} recorded times -- "
                                          f"no rank standards to grade with"})
                continue
            key = make_key(target, item["name"], item["ids"])
            self._rows[key] = entity
            adopted.append({"row_key": key,
                            "strategy": strategy_name(target["label"],
                                                      item["name"])})
        if not adopted:
            raise AdoptionError(
                f"{target['label']!r} has no approach with rank standards -- "
                f"linking it would grade nothing")
        save(self.path, self._rows)
        self._sync()
        return {"adopted": adopted, "skipped": skipped,
                "entity_key": entity, "target": target["label"]}

    def unadopt_target(self, index: int) -> dict:
        """Remove every one of this target's own approach assignments —
        never a piece's, and never another target's rows on the same
        segment."""
        targets = self.store.payload["targets"]
        target = targets[index] if 0 <= index < len(targets) else None
        if target is None:
            raise AdoptionError(f"no library target at index {index}")
        from sm64_events.library.audit import row_key as make_key
        removed = 0
        for item in target["approaches"]:
            key = make_key(target, item["name"], item["ids"])
            if self._rows.pop(key, None) is not None:
                removed += 1
        save(self.path, self._rows)
        self._sync()
        return {"removed": removed, "target": target["label"]}


def validate(payload: dict, key: str, entity: str, qualified=()):
    """Raise unless this row can be assigned to this entity.

    Every refusal names its reason: an assignment that silently does nothing is
    indistinguishable from one that worked until a rank fails to appear."""
    target, item, kind = find_row(payload, key)
    if target is None:
        raise AdoptionError(f"no library row called {key!r}")
    if not item.get("ladder"):
        samples = item.get("ladder_samples", len(item["entries"]))
        raise AdoptionError(
            f"{item['name']!r} has no rank standards -- only {samples} recorded "
            f"times, so adopting it would add a strategy that grades against "
            f"nothing")
    if entity in qualified:
        raise AdoptionError(
            f"{entity} names its strategies by exit-star variant, and the sheet "
            f"row does not say which exit star it ran")
    name = strategy_name(target["label"], item["name"], kind=kind)
    # A vetted strategy of the same name is NOT a refusal (round 6, reversing
    # round 5's arm): the standards read-merge keeps the vetted ladder
    # structurally, so the assignment cannot touch grading -- and it now
    # carries the row's DISPLAY association, which is exactly what the user
    # wants on a segment that already grades ("we should autoassign any
    # segments that exist already, and otherwise let them be associated by
    # hand", 2026-08-07).
    return target, item, name

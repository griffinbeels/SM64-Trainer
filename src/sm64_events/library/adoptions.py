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


def strategy_name(target_label: str, approach_name: str) -> str:
    return (DEFAULT_STRATEGY if approach_name.strip() == target_label.strip()
            else approach_name)


def _rows(payload):
    from sm64_events.library.audit import row_key
    for target in payload["targets"]:
        for kind in ("approaches", "subsections"):
            for item in target[kind]:
                yield target, item, row_key(target, item["name"], item["ids"])


def find_row(payload: dict, key: str):
    """(target, item) for one row key, or (None, None)."""
    for target, item, candidate in _rows(payload):
        if candidate == key:
            return target, item
    return None, None


def ladders(payload: dict, rows: dict) -> dict:
    """{entity key: {strategy: ladder}} for everything the user has assigned.

    A row with no fitted ladder contributes nothing: a strategy in the picker
    that grades against nothing is worse than not offering it. Two rows
    assigned to one segment simply become two strategies on it, which is the
    point — a movement with three documented ways to do it is three strategies."""
    out = {}
    for target, item, key in _rows(payload):
        entity = rows.get(key)
        if not entity or not item.get("ladder"):
            continue
        name = strategy_name(target["label"], item["name"])
        out.setdefault(entity, {}).setdefault(name, item["ladder"])
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
        vetted = {entity: self.standards._stored_ladders(entity)} \
            if self.standards is not None else {}
        target, item, name = validate(self.store.payload, key, entity,
                                      self.qualified, vetted)
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


def validate(payload: dict, key: str, entity: str, qualified=(), vetted=None):
    """Raise unless this row can be assigned to this entity.

    Every refusal names its reason: an assignment that silently does nothing is
    indistinguishable from one that worked until a rank fails to appear."""
    target, item = find_row(payload, key)
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
    name = strategy_name(target["label"], item["name"])
    if name in (vetted or {}).get(entity, {}):
        raise AdoptionError(
            f"{entity} already has a community-vetted strategy called {name!r}")
    return target, item, name

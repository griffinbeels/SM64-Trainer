"""Sheet rows, local practice entries, and their shared rank standards.

Explicit row assignments live in the user's data directory and outrank
automatic placement. placements.py resolves current local identities;
practice_catalog.py provisions missing movements and parented pieces.
All consumers use sheet_strategy for the row's canonical strategy slot.
"""
import json
import logging
import re
from pathlib import Path
from sm64_events.library.assignment_transaction import atomic_assignment, atomic_bytes

_log = logging.getLogger("sm64.library")

# What a strategy is called once adopted. A movement target usually holds one
# approach named after the target itself ("Lobby door (L) - BoB door"), and
# filing that under a segment of the same name reads as a stutter.
DEFAULT_STRATEGY = "Standard"


def _read(path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(path) -> dict:
    """{row key: entity key} — empty when absent or unreadable."""
    rows = _read(path).get("rows")
    if not isinstance(rows, dict):
        return {}
    return {key: value for key, value in rows.items()
            if isinstance(key, str) and isinstance(value, str) and value}


def save(path, rows: dict, *, unlinked=()) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(path, json.dumps({"version": 2, "rows": dict(sorted(rows.items())),
                                  "unlinked": sorted(unlinked)},
                                 indent=1, ensure_ascii=False).encode("utf-8"))


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


# What joins a qualifier to the row it qualifies: "the row it sits under › the
# row". Deliberately NOT the standards store's " · " -- that glyph means
# "exit-star variant · leaf" and `tests/test_single_source.py` keeps it to the
# modules that own that rule. This is a different fact about a different kind
# of row, so it wears a different mark and can never be misread as a variant.
ROUTE_SEP = " › "


def shares_its_entity(target: dict) -> bool:
    """Is this target one of SEVERAL the sheet maps onto one entity?

    True for every 100-coin star: `library/mapping.py` files every "+ 100c"
    row under `star:<course>:6`, and a course opens one target per route
    ending on a different star. Four targets cannot all be that entity's
    Standard, and their same-named sub-rows cannot all share one slot."""
    from sm64_events.library.mapping import HUNDRED_COIN_STAR
    key = target.get("entity_key") or ""
    return key.startswith("star:") and key.endswith(f":{HUNDRED_COIN_STAR}")


def _same_item(one: dict, other: dict) -> bool:
    return one is other or (one.get("name") == other.get("name")
                            and sorted(one.get("ids") or ())
                            == sorted(other.get("ids") or ()))


def _owning_approach(target: dict, item: dict):
    """For an approach whose NAME repeats inside its target, the approach it
    sits under; None for a name that appears once.

    The sheet writes a split row -- "Red coin star Xcam", "100 coin star
    Xcam" -- directly beneath the approach it splits, and stamps it with that
    approach's bracket ids. So the owner is the nearest PRECEDING approach of
    another name whose ids overlap this row's, and failing an overlap (one
    live row carries a bracket typo, `[4|6]` under Xiah's `[5|6]`) simply the
    nearest preceding one -- which is the sheet's own layout rule."""
    approaches = target.get("approaches") or []
    name = item.get("name")
    if sum(1 for other in approaches if other.get("name") == name) < 2:
        return None
    position = next((index for index, other in enumerate(approaches)
                     if _same_item(other, item)), len(approaches))
    preceding = [other for other in approaches[:position]
                 if other.get("name") != name]
    ids = set(item.get("ids") or ())
    for candidate in reversed(preceding):
        if ids & set(candidate.get("ids") or ()):
            return candidate
    return preceding[-1] if preceding else None


def sheet_strategy(target: dict, item: dict, kind: str = "approach") -> str:
    """The strategy an Ultimate Sheet row files under HERE -- ONE rule, read
    by the import (`library/import_runner.py`) and the column export
    (`library/export_column.py`) alike, so the two doors cannot disagree
    about which slot a row is.

    The rule is that every worksheet row of one entity gets a slot of its
    OWN. His ruling on the round-27 measurement, 2026-09-04 ("fix all bugs
    and maximize compatibility with the sheet"): the sheet is the finer
    record, and two rows the sheet keeps apart must not share one personal
    best. Three shapes collide without this, all measured on Raisn's column:

      * a row named after its target is that thing's STANDARD strategy
        (`strategy_name`) -- EXCEPT on a target that shares its entity (a
        100-coin star's four routes), where the target's own label is the
        slot, since four routes cannot all be "Standard";
      * a sub-row of such a shared target is qualified by its target --
        "Slip Slidin' Away + 100c › 100 coin star Xcam" -- because the
        course's other 100-coin routes each carry a row of the same name;
      * a row whose name REPEATS inside its own target is qualified by the
        approach it sits under -- "Xiah cycle pipe entry › Red coin star
        Xcam" -- because BitDW reds carries five "Red coin star Xcam" rows,
        one per pipe route, each timing a different run.

    A subsection is always its linked segment's Standard (the row names the
    piece being practised, not a way to perform it). A vetted match still
    outranks the sheet's own name on an ordinary strategy row."""
    label = (target.get("label") or "").strip()
    name = (item.get("name") or "").strip()
    if kind == "subsection":
        return DEFAULT_STRATEGY
    shared = shares_its_entity(target)
    if strategy_name(label, name) == DEFAULT_STRATEGY:
        return label if shared else DEFAULT_STRATEGY
    own = item.get("matched_strategy") or name
    owner = _owning_approach(target, item)
    if owner is not None:
        return f"{sheet_strategy(target, owner)}{ROUTE_SEP}{own}"
    return f"{label}{ROUTE_SEP}{own}" if shared else own


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
        name = sheet_strategy(target, item, kind)
        layers = out.setdefault(entity, {"strategies": {}, "jp_strategies": {}})
        layers["strategies"].setdefault(name, item["ladder"])
        if item.get("ladder_jp"):
            layers["jp_strategies"].setdefault(name, item["ladder_jp"])
        if item.get("ladder_estimate"):
            layers.setdefault("estimates", {})[name] = item["ladder_estimate"]
    return out


def library_ladders(payload: dict, rows: dict, qualified=()) -> dict:
    """{entity key: {"strategies": {name: ladder}, "jp_strategies": ...}}
    for EVERYTHING the library can grade: every star approach with a fitted
    ladder, under the slot the import files it in (`sheet_strategy` -- the
    star's own row is its Standard, a matched row wears its vetted twin's
    name), plus everything the user has assigned (`ladders`).

    Round 33 (2026-09-05), his report: "for a lot of the 'Standard' times,
    we are lacking rank standards." The bundled sheet layer was built by
    `adopt.adoptable`, which skips every approach the matcher paired with a
    vetted strategy -- and the star's own row is exactly such a pairing
    (A-Maze-Ing Emergency Exit's row matched Rightside at 182 samples) while
    round 28 files that row under Standard. So Standard held his PB and no
    ladder, and 95 star rows' fitted ladders never reached the store. This
    reads the CURRENT payload every time the library changes, so the layer
    follows the sheet rather than a release.

    Current fits supply the foundation on read. Explicit user edits survive
    per cutoff; unchanged materialized seed defaults yield to the Sheet.
    Shared 100-coin entities use route-qualified Sheet names, preserving
    distinct rows without inventing an exit-star variant. First fitted
    row per name wins, so a repeated name inside one
    target (already qualified by `sheet_strategy`) cannot overwrite."""
    from sm64_events.library.placements import row_identity
    out = {}
    for target, item, _key, kind in _rows(payload):
        identity = row_identity(target, item, kind, rows)
        if identity is None or not item.get("ladder"):
            continue
        entity, name = identity
        layers = out.setdefault(entity, {"strategies": {}, "jp_strategies": {}})
        layers["strategies"].setdefault(name, item["ladder"])
        if item.get("ladder_jp"):
            layers["jp_strategies"].setdefault(name, item["ladder_jp"])
        if item.get("ladder_estimate"):
            layers.setdefault("estimates", {})[name] = item["ladder_estimate"]
    return out


class AdoptionError(ValueError):
    """The assignment cannot be made, and the caller needs to hear why."""


class Adoptions:
    """The user's assignments, and the one place they reach the ranker.

    Holds the wiring so the router does not: load the file, validate against
    the live library, and re-merge the resulting ladders into the standards
    store on every change. Re-merging rather than appending is what makes an
    unadopt actually remove a strategy."""

    def __init__(self, path, store, standards, qualified=(), segment_defs=None,
                 provision=None, policy=None):
        self.path = Path(path)
        self.store = store               # LibraryStore
        self.standards = standards       # RankStandards
        self.qualified = set(qualified)
        self._rows = {}
        self._unlinked = set()
        self.segment_defs = segment_defs
        self.provision = provision
        self._automatic = {}
        self.policy = policy
        if standards is not None and hasattr(store, "calibrations"):
            standards.calibrations = store.calibrations
            store.prepare_calibration = self._prepare_calibration

    @atomic_assignment
    def load(self) -> None:
        self._rows = load(self.path)
        self._unlinked = {key for key in _read(self.path).get("unlinked", [])
                          if isinstance(key, str)} - self._rows.keys()
        self._sync()

    def rows(self) -> dict:
        """Resolved assignments; empty values reserve explicitly unlinked rows.

        The reservation prevents downstream automatic placement from putting
        a deliberately unlinked row back. Consumers resolve through row_identity.
        """
        generation = getattr(self.store, "calibrations", None)
        if generation is not None and generation.read is not None:
            return dict(generation.read.rows)
        return self._resolved_rows(self.store.payload, self._automatic)

    def _resolved_rows(self, payload, automatic):
        from sm64_events.library.placements import automatic_rows
        definitions = list(self.segment_defs()) if self.segment_defs else []
        existing = {f"segment:{d['id']}" for d in definitions}
        generated = {key: entity for key, entity in automatic.items()
                     if entity in existing}
        return automatic_rows(payload,
                              {**generated, **dict.fromkeys(self._unlinked, ""),
                               **self._rows}, definitions)

    def _save(self):
        save(self.path, self._rows, unlinked=self._unlinked)

    def ladders(self) -> dict:
        """Every sheet-fitted ladder the store should carry: the whole
        library's (round 33) plus the user's assignments."""
        return library_ladders(self.store.payload, self.rows(), self.qualified)

    def _sync(self) -> None:
        if self.standards is not None and hasattr(self.store, "recalibrate"):
            self.store.recalibrate()
            return
        if self.provision is not None:
            self._automatic = self.provision(self.store.payload, self._rows)
        if self.standards is not None:
            self.standards.apply_sheet_ladders(self.ladders())

    def _prepare_calibration(self, payload):
        from sm64_events.library.calibration import prepare
        from sm64_events.ranks.policy import RankingPolicy
        automatic = self.provision(payload, self._rows) if self.provision else {}
        assignments = self._resolved_rows(payload, automatic)
        definitions = list(self.segment_defs()) if self.segment_defs else []
        policy = self.policy() if callable(self.policy) else self.policy
        candidate = prepare(payload, assignments, definitions, self.standards,
                            policy or RankingPolicy())
        self._automatic = automatic
        return candidate

    @atomic_assignment
    def adopt(self, key: str, entity: str) -> dict:
        target, item, name = validate(self.store.payload, key, entity,
                                      self.qualified)
        self._rows[key] = entity
        self._unlinked.discard(key)
        self._save()
        self._sync()
        return {"adopted": True, "row_key": key, "entity_key": entity,
                "strategy": name, "ladder": item["ladder"],
                "target": target["label"]}

    @atomic_assignment
    def unadopt(self, key: str) -> dict:
        removed = self.rows().get(key)
        self._rows.pop(key, None)
        self._unlinked.add(key)
        self._save()
        self._sync()
        return {"adopted": False, "row_key": key, "entity_key": removed}

    def linked_targets(self) -> dict:
        """{entity key: [{index, label}]} — the REVERSE of resolved rows,
        for the segment editor's "which library target points at me" view
        (round 8). Computed from APPROACH assignments only: a piece link is
        a partial fact and must not present a whole target as linked."""
        from sm64_events.library.audit import row_key as make_key
        out = {}
        assigned = self.rows()
        for position, target in enumerate(self.store.payload["targets"]):
            entities = {assigned[key]
                        for item in target["approaches"]
                        if (key := make_key(target, item["name"],
                                            item["ids"])) in assigned and assigned[key]}
            for entity in entities:
                out.setdefault(entity, []).append(
                    {"index": position, "label": target["label"]})
        return out

    @atomic_assignment
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
            self._unlinked.discard(key)
            adopted.append({"row_key": key,
                            "strategy": sheet_strategy(target, item)})
        if not adopted:
            raise AdoptionError(
                f"{target['label']!r} has no approach with rank standards -- "
                f"linking it would grade nothing")
        self._save()
        self._sync()
        return {"adopted": adopted, "skipped": skipped,
                "entity_key": entity, "target": target["label"]}

    @atomic_assignment
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
        assigned = self.rows()
        for item in target["approaches"]:
            key = make_key(target, item["name"], item["ids"])
            if assigned.get(key):
                removed += 1
            self._rows.pop(key, None)
            self._unlinked.add(key)
        self._save()
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
    name = sheet_strategy(target, item, kind)
    # A vetted strategy of the same name is NOT a refusal (round 6, reversing
    # round 5's arm): the standards read-merge keeps the vetted ladder
    # structurally, so the assignment cannot touch grading -- and it now
    # carries the row's DISPLAY association, which is exactly what the user
    # wants on a segment that already grades ("we should autoassign any
    # segments that exist already, and otherwise let them be associated by
    # hand", 2026-08-07).
    return target, item, name

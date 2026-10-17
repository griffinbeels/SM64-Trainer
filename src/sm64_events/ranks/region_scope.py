"""Region scope: whether a (target, strategy) plays the same on US and JP.

His rule, task 0064: *"if the stars are the same for US/JP (no difference),
then ALL ghosts are valid for that star, and we should have inter-region
ghosts. But, if the star is differently timed / there's clearly a regional
difference, then the ghosts should be region locked to only your version's
ghosts."* And: *"the user should NEVER have to think about this manually; it
should just work."*

So the answer is derived, never set:

* **shared** -- the standards for that strategy name no regional difference.
  Ghosts recorded on either ROM may be served, and one personal best covers
  both ROMs (the faster wins).
* **regional** -- the strategy has a JP delta (`RankStandards.jp_deltas`, from
  the vetted seed or the Sheet's fitted JP ladder), or exists on one ROM only
  (`RankStandards.single_version_strategies`, the Sheet's region-tagged rows).
  Only the running ROM's ghosts are served and its own personal best counts.

A strategy with no evidence (a custom strategy, an empty ladder) follows its
entity: regional when any strategy there is regional, shared when the entity
has evidence and none is, and regional when the entity has none at all --
never serve a ghost from the other ROM without evidence it plays the same.

A ghost whose ROM is unknown is served only under a shared scope (his ruling,
2026-10-12: "If it's not a perfectly recorded ghost, it doesn't belong in the
system"). Scope only widens which ROM's ghosts may be SERVED; a ghost is still
captured on exactly one ROM (`ghost/pb.py::eligible_capture`).
"""
from __future__ import annotations

from threading import Lock
from weakref import WeakKeyDictionary

SHARED = "shared"
REGIONAL = "regional"
SCOPES = (SHARED, REGIONAL)
VERSIONS = ("us", "jp")

_bound = None
_cache = WeakKeyDictionary()
_lock = Lock()


def bind(standards) -> None:
    """The store `scope_of` reads when a caller names none. The tracker
    service binds its own at construction, so the app needs no wiring."""
    global _bound
    _bound = standards


def _evidence(standards, ek) -> dict:
    """{strategy: scope} for every strategy with region evidence."""
    ladders = getattr(standards, "ladders", None)
    if not callable(ladders):
        return {}      # a store that cannot answer is no evidence
    base = ladders(ek, "us")
    deltas = getattr(standards, "jp_deltas", None)
    deltas = deltas if callable(deltas) else (lambda _ek, _strat: {})
    single = getattr(standards, "single_version_strategies", None)
    single = single(ek) if callable(single) else {}
    scopes = {}
    for strat in dict.fromkeys([*base, *single]):
        if deltas(ek, strat) or strat in single:
            scopes[strat] = REGIONAL
        elif base.get(strat):
            scopes[strat] = SHARED
    return scopes


def _entity(standards, ek) -> dict:
    revision = getattr(standards, "region_revision", None)
    if revision is None:
        return _evidence(standards, ek)
    with _lock:
        cached = _cache.get(standards)
        if cached is None or cached[0] != revision:
            cached = (revision, {})
            _cache[standards] = cached
        found = cached[1].get(ek)
    if found is None:
        found = _evidence(standards, ek)
        with _lock:
            cached[1][ek] = found
    return found


def scope_of(entity_key: str, strategy: str | None, standards=None) -> str:
    """"shared" or "regional" for one entity key (`star:C:S` / `segment:N`)
    and strategy. `standards` defaults to the bound store."""
    standards = _bound if standards is None else standards
    if standards is None:
        return REGIONAL
    scopes = _entity(standards, entity_key)
    if strategy in scopes:
        return scopes[strategy]
    if not scopes or REGIONAL in scopes.values():
        return REGIONAL
    return SHARED


def widest_scope(entity_key: str, standards=None) -> str:
    """Shared when ANY strategy of the entity is: what a choice mixing
    strategies (the Overall ghost tab) fetches before `scope_of` judges each
    ghost by its own strategy."""
    standards = _bound if standards is None else standards
    if standards is None:
        return REGIONAL
    return SHARED if SHARED in _entity(standards, entity_key).values() else REGIONAL


def serves(scope: str, running: str | None, recorded: str | None) -> bool:
    """Whether a ghost recorded on `recorded` (None = unknown) may be served
    while `running` plays under `scope`."""
    if scope == SHARED:
        return recorded in (None, *VERSIONS)
    return running in VERSIONS and recorded == running


def servable_versions(scope: str, running: str) -> tuple:
    """The recorded ROMs a ghost provider returns; None stands for unknown."""
    return (*VERSIONS, None) if scope == SHARED else (running,)


def checked(scope: str) -> str:
    if scope not in SCOPES:
        raise ValueError(f"unknown region scope {scope!r}")
    return scope

"""GHOST FILES: the room ghost files take on this computer, and the two Settings buttons that free it.

His words, 2026-11-08: *"Maybe we need an extra button in the settings menu for purging all
non-PB ghost files (and another for purging all local ghost files)? This wouldn't clear the
server, but would free up space for the user."*

One owner per kind of file, and this module builds none of their paths: each owner lists its
holdings (every file it could delete, with its size and its paths) and deletes the ones it is
handed, skipping what Windows will not let go of.

| Kind | Owner | Ghost it counts as |
|---|---|---|
| attempt scenes (lab only) | `tools/scene_lab/attempt_scenes.py` `AttemptScenes` | the attempt |
| his recorded runs (`native/attempt_N.*` and the playback copy beside it) | `replay/service.py` `ReplayService.native_holdings` / `remove_native` | the attempt |
| his ghost population (`ghost_population_root()`) | `ghost/population.py` `LocalPopulation.holdings` / `remove` | the attempt it came from, else the row |
| downloaded community ghosts (packaged builds) | `community/cache.py` `CachedPopulation.cached_holdings` / `drop_cached` | the recording |
| imported ghosts (`shared-ghosts/`) | `ghost/sharing.py` `GhostSharing.holdings` / `remove` | the import |
| ghost starts (`starts/` beside the population) | `ghost/start_frame.py` `holdings` / `remove_holdings` | its clip's ghost |

A ghost start (the game state a ghost begins from, kept so the lab can re-simulate it) follows its clip: it
counts in the ghost that clip counts in, is kept and pinned with it, and goes only when every clip of its
sha goes (a population row and his recorded run may share the bytes).

"Keep PBs" keeps, besides every kept attempt's files: each PB (the latest visible save of every
star and segment, per strategy and per ROM, so whichever ROM runs and whichever slot grades, the
current PB keeps its ghost), every ghost he saved by hand (`ghost/saved.py`), the runs his input
templates were marked from, and every imported ghost (an import is a choice, like a save). The
other button deletes those too. A superseded PB loses its ghost to "keep PBs": an Undo then
brings back a PB that ranks but races no ghost.

Neither button ever deletes (counted in `total`, never in a button's number):
- a file a pending community upload names (`CommunitySync.pending_upload_paths`): his uploads;
- the recorded run of an attempt the admission worker still owes (`LocalPopulation.owed_runs`);
- the recorded run that keeps an unsorted attempt in his log. The startup prune
  (`tracking/prune.py`) spares an attempt with no strategy only for a saved PB, a saved video
  or this clip, so deleting the clip would delete the attempt's time at the next start.
Never ghost files here: the recorder's rolling pose archive (session scratch a PB save reads),
a saved replay's own poses beside its video, saved replays and their video, scene clips, the Slot
Library's save states, the journal and every setting.

In use right now (racing in the game, open in the Scene viewer, a scene's sound still being
added): left out of both buttons' numbers and skipped by a purge, which says how many.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("sm64.ghost")

#: How far back the owed table is read for runs a purge must not touch.
OWED_LIMIT = 4096
#: (path, size, mtime) -> sha256 of a clip a start may belong to (Settings asks often; clips rarely change).
_CLIP_SHAS: dict = {}
_PURGING = threading.Lock()


@dataclass
class Holding:
    """One deletable thing an owner keeps: `ghost` is what the user counts (an attempt, a row)."""
    kind: str
    ref: object
    ghost: tuple
    bytes: int
    paths: tuple = ()
    attempt: int | None = None
    keep: bool = False          # kept by "keep PBs"
    pinned: bool = False        # never deleted by either button
    busy: bool = False          # in use right now
    shares: tuple = field(default_factory=tuple)   # population: clip bytes counted once per digest
    owners: tuple = field(default_factory=tuple)   # a start: id() of every clip holding of its sha


def _normal(path) -> str:
    import os
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(str(path))


def kept_attempts(database) -> set[int]:
    """The attempts "keep PBs" keeps: each PB slot's latest visible save, hand-saved ghosts and
    template runs. A slot is (star or segment, timer, strategy, ROM): every answer the practice
    lookup (`tracking/views.py::current_pbs_by_strat`, `ghost/pb.py::current_pb_ghost`) can give
    under any running ROM or region scope is the latest save of one of these slots."""
    from sm64_events.ghost.pb import attempt_id_of
    from sm64_events.ghost.saved import saved_ids
    from sm64_events.tracking.views import pb_rom
    if database is None:
        return set()
    latest = {}
    for row in database.pbs():   # id order: later saves win
        if row.get("attempt_id"):
            latest[(row["course_id"], row["star_id"], row.get("segment_id"), row["timer_mode"],
                    row.get("strat_tag"), pb_rom(row))] = row["attempt_id"]
    kept = set(latest.values()) | set(saved_ids(database))
    templates = getattr(database, "input_templates", None)
    if templates is not None:
        for template in templates().all():
            number = attempt_id_of(template.origin)
            if number is not None:
                kept.add(number)
    return kept


def _layers(population):
    """The population and every store it wraps (community cache -> bundled -> local)."""
    seen = []
    while population is not None and population not in seen:
        seen.append(population)
        population = getattr(population, "local", None)
    return seen


class GhostStorage:
    """The two purges over the owners a ghost session holds. Every method blocks (folder walks,
    file deletes): call it off the event loop. One purge runs at a time."""

    def __init__(self, *, database, replay=None, population=None, sharing=None, scenes=None,
                 community=None, in_use=lambda: ()):
        """Each source a callable (a late database or a lab that is off answers None)."""
        self.database, self.replay, self.population = database, replay, population
        self.sharing, self.scenes, self.community, self.in_use = sharing, scenes, community, in_use
        self.lock = _PURGING                    # one purge at a time, whichever request made this

    @staticmethod
    def _call(source):
        try:
            return source() if callable(source) else source
        except (RuntimeError, ValueError, OSError):
            return None

    # -- what is there ---------------------------------------------------------

    def holdings(self) -> list[Holding]:
        database = self._call(self.database)
        keep = kept_attempts(database)
        busy = {_normal(path) for path in (self._call(self.in_use) or ())}
        named = self._pending_uploads()
        if named is None:                           # unreadable: no file an upload may name goes
            uploads, digests, blind = set(), set(), True
        else:
            uploads, digests, blind = {_normal(path) for path in named[0]}, set(named[1]), False
        found: list[Holding] = []
        for kind, listing in (("scene", self._scenes), ("recorded", lambda: self._recorded(database)),
                              ("population", lambda: self._population(database)),
                              ("community", self._community), ("import", self._imports)):
            try:
                found += listing()
            except Exception:  # noqa: BLE001 - a kind that cannot be listed is neither shown nor deleted
                log.warning("Ghost files of kind %s could not be listed.", kind, exc_info=True)
        for holding in found:
            normal = {_normal(path) for path in holding.paths}
            holding.busy = holding.busy or bool(normal & busy)
            holding.pinned = (holding.pinned or bool(normal & uploads) or bool(set(holding.shares) & digests)
                              or (blind and holding.kind in ("recorded", "population")))
            holding.keep = holding.kind == "import" or (holding.attempt is not None and holding.attempt in keep)
        try:
            found += self._starts(found)
        except Exception:  # noqa: BLE001 - starts that cannot be listed are neither shown nor deleted
            log.warning("Ghost starts could not be listed.", exc_info=True)
        return found

    def _starts_root(self):
        store = self._local_store()
        return getattr(store, "root", None)

    def _clip_shas(self, holding: Holding) -> set[str]:
        """The sha256 of every clip a holding deletes: the digest it names, else its .ghost files hashed
        (remembered by path, size and time, so Settings reads each clip once)."""
        import hashlib
        if holding.shares:
            return set(holding.shares)
        found = set()
        for path in holding.paths:
            path = Path(path)
            if path.suffix != ".ghost":
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            key = (_normal(path), stat.st_size, stat.st_mtime_ns)
            digest = _CLIP_SHAS.get(key)
            if digest is None:
                digest = _CLIP_SHAS[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            found.add(digest)
        return found

    def _starts(self, found: list[Holding]) -> list[Holding]:
        from sm64_events.ghost import start_frame
        root = self._starts_root()
        rows = start_frame.holdings(root) if root is not None else []
        if not rows:
            return []
        wanted = {row["sha256"] for row in rows}
        owners = defaultdict(list)
        for holding in found:
            if holding.kind in ("recorded", "population", "community", "import"):
                for digest in self._clip_shas(holding) & wanted:
                    owners[digest].append(holding)
        starts = []
        for row in rows:
            mine = owners.get(row["sha256"], [])
            starts.append(Holding("start", row["sha256"], mine[0].ghost if mine else ("start", row["sha256"]),
                                  row["bytes"], (row["path"],), attempt=mine[0].attempt if mine else None,
                                  keep=any(h.keep for h in mine), pinned=any(h.pinned for h in mine),
                                  busy=any(h.busy for h in mine), owners=tuple(id(h) for h in mine)))
        return starts

    def _pending_uploads(self):
        """(paths, digests) of unsent uploads, ((), ()) with no community, None when unreadable."""
        community = self._call(self.community)
        named = getattr(community, "pending_uploads", None)
        if named is None:
            return (), ()
        try:
            return named()
        except Exception:  # noqa: BLE001 - an outbox that cannot be read protects everything it might name
            log.warning("The pending ghost uploads could not be read.", exc_info=True)
            return None

    def _scenes(self) -> list[Holding]:
        scenes = self._call(self.scenes)
        if scenes is None:
            return []
        return [Holding("scene", row["attempt"], ("attempt", row["attempt"]), row["bytes"],
                        attempt=row["attempt"], busy=row["in_use"]) for row in scenes.holdings()]

    def _recorded(self, database) -> list[Holding]:
        replay = self._call(self.replay)
        if replay is None or not hasattr(replay, "native_holdings"):
            return []
        rows = replay.native_holdings()
        owed = self._owed()
        owed = None if owed is None else {row["attempt_id"] for row in owed}
        keeps_log = self._keeps_an_unsorted_attempt(database, replay, [row["attempt"] for row in rows])
        return [Holding("recorded", row["attempt"], ("attempt", row["attempt"]), row["bytes"],
                        tuple(row["paths"]), attempt=row["attempt"],
                        pinned=owed is None or row["attempt"] in owed or row["attempt"] in keeps_log)
                for row in rows]

    def _local_store(self):
        population = self._call(self.population)
        return next((layer for layer in _layers(population) if hasattr(layer, "holdings")
                     and hasattr(layer, "rows_for_source")), None)

    def _owed(self):
        """The runs the admission worker still owes, () with no population, None when unreadable
        (then no recorded run goes: any of them may be owed)."""
        store = self._local_store()
        try:
            return store.owed_runs(OWED_LIMIT) if store is not None else ()
        except Exception:  # noqa: BLE001 - an unreadable owed table protects every run it might name
            log.warning("The owed ghost runs could not be read.", exc_info=True)
            return None

    @staticmethod
    def _keeps_an_unsorted_attempt(database, replay, attempts) -> set[int]:
        """The attempts whose committed clip is all that keeps them through the startup prune."""
        from sm64_events.tracking.prune import unlabelled
        if database is None or not attempts:
            return set(attempts)          # no journal to ask: delete no clip that may hold an attempt
        protected = set(database.pb_attempt_ids())
        root = getattr(getattr(replay, "cfg", None), "save_root", None)
        if root is not None:
            from sm64_events.replay.service import saved_attempt_ids
            protected |= saved_attempt_ids(Path(root), include_native=False, among=attempts)
        held = set()
        for number in attempts:
            attempt = database.attempt(number)
            if attempt is not None and number not in protected and unlabelled(attempt):
                held.add(number)
        return held

    def _population(self, database) -> list[Holding]:
        """Each row counts as the attempt it came from only when this journal's attempt of that
        number has the same lineage (`ghost/pb.py::local_attempt_source`): a fresh profile reuses
        attempt numbers, and another profile's row is no run of his here."""
        from sm64_events.ghost.pb import local_attempt_source
        store = self._local_store()
        if store is None:
            return []
        found = []
        for row in store.holdings():
            source = row.get("source")
            number = source.get("attempt_id") if isinstance(source, dict) else None
            attempt = database.attempt(number) if database is not None and type(number) is int else None
            mine = number if attempt is not None and local_attempt_source(attempt) == source else None
            found.append(Holding("population", row["id"], ("attempt", mine) if mine is not None
                                 else ("population", row["id"]), row["bytes"], (row["path"],), attempt=mine,
                                 shares=(row["sha256"],)))
        return found

    def _community(self) -> list[Holding]:
        population = self._call(self.population)
        cache = next((layer for layer in _layers(population) if hasattr(layer, "cached_holdings")), None)
        if cache is None:
            return []
        return [Holding("community", row["id"], ("community", row["id"]), row["bytes"], tuple(row["paths"]),
                        busy=row["in_use"]) for row in cache.cached_holdings()]

    def _imports(self) -> list[Holding]:
        sharing = self._call(self.sharing)
        if sharing is None or not hasattr(sharing, "holdings"):
            return []
        return [Holding("import", row["id"], ("import", row["id"]), row["bytes"], (row["path"],),
                        shares=(row["sha256"],)) for row in sharing.holdings()]

    # -- the numbers Settings shows -------------------------------------------

    @staticmethod
    def _deletable(holdings, keep_pbs: bool) -> list[Holding]:
        """A start goes only with every clip of its sha (a start whose clips are all gone already goes too)."""
        chosen = [holding for holding in holdings if holding.kind != "start"
                  and not holding.pinned and not holding.busy and not (keep_pbs and holding.keep)]
        ids = {id(holding) for holding in chosen}
        return chosen + [holding for holding in holdings if holding.kind == "start"
                         and not holding.pinned and not holding.busy and not (keep_pbs and holding.keep)
                         and all(owner in ids for owner in holding.owners)]

    @staticmethod
    def _freed(holdings, chosen) -> dict:
        """What deleting `chosen` frees: a shared clip only when every row sharing it goes."""
        chosen_ids = {id(holding) for holding in chosen}
        staying = defaultdict(bool)
        for holding in holdings:
            for digest in holding.shares:
                staying[(holding.kind, digest)] |= id(holding) not in chosen_ids
        total, counted = 0, set()
        for holding in chosen:
            if not holding.shares:
                total += holding.bytes
                continue
            for digest in holding.shares:
                key = (holding.kind, digest)
                if not staying[key] and key not in counted:
                    counted.add(key)
                    total += holding.bytes
        # A start counts in its clip's ghost; one whose clips are all gone already is bytes, not a ghost.
        return {"ghosts": len({holding.ghost for holding in chosen if holding.kind != "start" or holding.owners}),
                "bytes": total}

    def usage(self) -> dict:
        """GET /api/ghost/storage: what ghost files use, and what each button would free now."""
        holdings = self.holdings()
        return {"available": True, "total": self._freed(holdings, holdings),
                "except_pbs": self._freed(holdings, self._deletable(holdings, True)),
                "all": self._freed(holdings, self._deletable(holdings, False)),
                "in_use": self._freed(holdings, [holding for holding in holdings if holding.busy])}

    # -- the purges ------------------------------------------------------------

    def purge(self, *, keep_pbs: bool) -> dict:
        """Deletes every deletable holding (all but PBs, saved and imported ghosts with `keep_pbs`).
        {"deleted": ghosts, "bytes": freed, "skipped": ghosts in use, "usage": usage()}. RuntimeError
        when a purge is already running."""
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Ghost files are already being deleted.")
        try:
            holdings = self.holdings()
            chosen = self._deletable(holdings, keep_pbs)
            busy = {holding.ghost for holding in holdings
                    if holding.busy and not holding.pinned and not (keep_pbs and holding.keep)}
            by_kind = defaultdict(list)
            for holding in chosen:
                by_kind[holding.kind].append(holding)
            deleted, skipped, freed, gone_ids = set(), set(busy), 0, set()
            starts = by_kind.pop("start", [])
            for kind, rows in by_kind.items():
                result = self._delete(kind, rows)
                freed += result["bytes"]
                gone = set(result["deleted"])
                for holding in rows:
                    (deleted if holding.ref in gone else skipped).add(holding.ghost)
                    if holding.ref in gone:
                        gone_ids.add(id(holding))
            # A start goes after its clips, and only when every one of them went.
            starts = [holding for holding in starts if all(owner in gone_ids for owner in holding.owners)]
            if starts:
                result = self._delete("start", starts)
                freed += result["bytes"]
                gone = set(result["deleted"])
                for holding in starts:
                    if holding.ref not in gone:
                        skipped.add(holding.ghost)
            # A ghost is deleted only when none of its files stayed behind.
            deleted -= skipped
            log.info("ghost files purged: %d ghosts, %d bytes freed, %d skipped (keep_pbs=%s)",
                     len(deleted), freed, len(skipped), keep_pbs)
            return {"deleted": len(deleted), "bytes": freed, "skipped": len(skipped), "usage": self.usage()}
        finally:
            self.lock.release()

    def _delete(self, kind: str, rows: list[Holding]) -> dict:
        refs = [holding.ref for holding in rows]
        try:
            if kind == "scene":
                return self._call(self.scenes).purge(refs)
            if kind == "recorded":
                return self._call(self.replay).remove_native_many(refs)
            if kind == "population":
                store = self._local_store()
                return store.remove_holdings(refs)
            if kind == "community":
                population = self._call(self.population)
                cache = next(layer for layer in _layers(population) if hasattr(layer, "drop_cached"))
                return cache.drop_cached(refs)
            if kind == "import":
                return self._call(self.sharing).remove(refs)
            if kind == "start":
                from sm64_events.ghost import start_frame
                return start_frame.remove_holdings(self._starts_root(), refs)
        except Exception:  # noqa: BLE001 - one owner's failure never stops the others
            log.warning("Ghost files of kind %s could not be deleted.", kind, exc_info=True)
        return {"deleted": [], "bytes": 0, "skipped": refs}

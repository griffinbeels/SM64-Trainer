"""The Scene viewer's stage and ghost catalog (M4 round 4, lab only): which of his attempt scenes
is the stage for a course, star and strategy, which ghosts could be drawn into it, and each
listed ghost's recording for the renderer.

    GET /api/scene-lab/stage?course=&star=&strat=        (or ?segment=&strat=)
    GET /api/scene-lab/ghosts?section=yours|top|search|player&course=&star=&strat=&page=N
                             (section=top&strategies=all: every strategy at once, `every_top`)
    SceneLab.ghosts.load_ghost(ref) -> {"clip", "player", "frames", "label"}; LookupError says why

server/scene_lab_api.py mounts the routes and owns the one SceneGhosts (`SceneLab.ghosts`); this
module is imported only while the lab switch is on. It is server code rather than a
tools/scene_lab module because everything it reads lives only in the server process: the
tracker's journal, the replay service's committed Mario poses and the ghost race's population.

The stage rule (decided 2026-10-27): the stage is the scene the user opened; for a picked target
it is his fastest finished attempt that still has an attempt scene (a reset or death only when no
finished one has a scene), else a plain reason. The strategy defaults to the one he last chose for
the target (the Practice tab's per-target memory).

A ghost here is one recording the in-game race could play for this target and strategy, read
through the race's own doors, so the viewer never lists a ghost the game would refuse:
- his runs: journal attempts that count as a best (`library_bests._eligible`) whose committed
  Mario poses `ReplayService.find_native` validates and whose playback copy (`native_with_inputs`)
  knows every input: the native session's own test (`NativeGhost._resolve_template`);
- the rival population (`Rivals.population`): certified local runs, the bundled collection and
  the community's cached recordings for this identity, played through `population.resolve`;
- imported ghosts (`GhostSharing.imported_rows`), played through `GhostSharing.resolve`.
Anything else is left out and counted (`exclusions()`, and the log), never listed with a warning:
his rule, 2026-10-12, "If it's not a perfectly recorded ghost, it doesn't belong in the system."
A source checkout (the only place the lab runs) has no community and no bundled collection, so
there the population holds only his own certified runs; other players arrive by imported collection.

Rank: the place a ghost's time holds among every player's best ghost in this catalog (1 is the
fastest; a tie shares its place, 1-2-2-4). Tier and division: the rank that time earns on the
strategy's ladder (`adaptive.rank_frames`, the Lineup's rank cap), null without a ladder.

Refs are opaque ("g" and 20 hex digits of a keyed hash of the source), stable while this server
runs, never a path; load_ghost refuses a ref this server never listed.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import os
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sm64_events.core.timefmt import format_igt_short
from sm64_events.memory.addresses import course_name, star_count, star_name

log = logging.getLogger("sm64.scene_lab")

PAGE_SIZE = 10
SECTIONS = ("yours", "top", "search", "player")
SORTS = ("fastest", "newest")
YOU = ("you",)                     # the player key of every ghost that is his
NO_SCENE = ("No saved scene for this strategy yet. Play it with scene recording on, and its scene appears here.")
NO_STRATEGY = "Choose a strategy to see its ghosts."
NO_YOURS = "You have no ghost recordings for this strategy yet."
NO_GHOSTS = "No ghosts for this strategy yet."
NO_GHOSTS_ANY = "No ghosts here yet."
NO_MATCH = "No player by that name has a ghost for this strategy."
NO_QUERY = "Type a player's name to search."
NO_PLAYER = "That player has no ghosts for this strategy."
NO_RECORDINGS = "Ghost recordings are not available in this window."
UNLISTED = "That ghost is not in the list any more; pick it again."
NOT_HELD = "That ghost could not be found. Open it from the Library again."
OTHER_TARGET = "That ghost is for a different star or segment."
NOT_SAVED = "This run's Mario poses are not saved."
MAX_CLIP_BYTES = 256 << 20        # far above any real clip (100,000 records): a bound, not a limit


class TargetError(ValueError):
    """The page named no target, one that does not exist, or a section it cannot ask for."""


@dataclass(frozen=True)
class Target:
    """One star (course_id, star_id; star_id counts from 0) or one segment (segment_id)."""
    kind: str
    course_id: int | None = None
    star_id: int | None = None
    segment_id: int | None = None

    @property
    def entity_key(self) -> str:
        """The ghost and input layers' spelling (`inputs/service.py::entity_key_of`)."""
        return f"{self.course_id}-{self.star_id}" if self.kind == "star" else str(self.segment_id)

    @classmethod
    def of(cls, attempt) -> "Target":
        if attempt.segment_id is not None:
            return cls("segment", segment_id=attempt.segment_id)
        return cls("star", course_id=attempt.course_id, star_id=attempt.star_id)

    def owns(self, attempt) -> bool:
        return Target.of(attempt) == self


@dataclass(frozen=True)
class Ghost:
    """One listed recording. `source` names where its bytes come from: ("attempt", id),
    ("population", id) or ("import", id); `who` the player it belongs to (YOU for his);
    `version` the ROM it was recorded on ("us" or "jp"), None where its record does not say."""
    ref: str
    source: tuple
    player: str
    who: object
    frames: int
    recorded_utc: str | None
    yours: bool
    target: dict = field(compare=False)
    attempt: int | None = None       # his journal attempt behind it, when it is one
    version: str | None = None


def region(value) -> str | None:
    """A recorded ROM as the page draws its flag: "us" or "jp", else None (never a default).

    Where each source keeps it: his runs, the attempt's `game_version` (the poller stamps every
    event with the running ROM; None for a run journaled before that stamp, or one whose first and
    last events disagree, tracking/projection.py); a population row, its `game_version`, and a
    bundled one its `recorded_game_version` (its `game_version` is only the query's ROM,
    ghost/bundled_beta.py); a community row, its required `game_version`; an imported ghost, its
    collection row's `game_version` (absent from a version-1 collection, ghost/portable.py)."""
    return value if value in ("us", "jp") else None


def _instant(text) -> float:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _folded(name) -> str:
    return unicodedata.normalize("NFKC", name).casefold().strip() if isinstance(name, str) else ""


def bare_time(frames) -> str | None:
    """The project's time display (`timefmt.format_igt_short`: 11"03, 1'02"16)."""
    return format_igt_short(frames) if type(frames) is int and frames > 0 else None


def _fastest(ghost) -> tuple:
    return ghost.frames, _instant(ghost.recorded_utc), ghost.ref


def _places(ghosts) -> dict:
    """Each player's best time: the basis of every ghost's rank."""
    best = {}
    for ghost in ghosts:
        if ghost.who not in best or ghost.frames < best[ghost.who]:
            best[ghost.who] = ghost.frames
    return best


def _rank(ghost, best: dict) -> int:
    """1 + the players other than the ghost's own whose best is faster (competition ranking)."""
    return 1 + sum(frames < ghost.frames for who, frames in best.items() if who != ghost.who)


class SceneGhosts:
    """The Scene viewer's stage choice, target names and ghost catalog.

    `tracker` is the TrackerService (journal, per-target strategy memory, rank standards);
    `scenes()` the attempt scenes (attempt_scenes.AttemptScenes, or None); `replay` the
    ReplayService (or None); `ghost` the GhostSession (or None) whose rival race holds the
    population and knows which players are his. Blocking: the routes call it off the loop."""

    def __init__(self, tracker=None, *, scenes=lambda: None, replay=None, ghost=None, community=None):
        self.tracker, self.scenes_of, self.replay, self.ghost = tracker, scenes, replay, ghost
        self.sync = community                       # the race's CommunitySync, or None (a source checkout)
        self._community = None
        self._key = os.urandom(16)
        self._lock = threading.Lock()
        self._refs: dict[str, Ghost] = {}
        self._runs: dict[int, tuple] = {}         # attempt id -> (stamp, (path, manifest) | reason)
        self._refused: dict[str, str] = {}        # ref -> why its recording failed to load
        self._exclusions: dict[tuple, dict] = {}  # (target, strat) -> the last listing's counts
        self._warming: set[tuple] = set()          # (target, strat) being checked in the background
        self._snapshots: dict[tuple, tuple] = {}   # population query -> (its generation, rows)

    # ------------------------------------------------------------ targets and names

    @property
    def db(self):
        return getattr(self.tracker, "db", None)

    @property
    def rivals(self):
        return getattr(self.ghost, "rivals", None)

    @property
    def community(self):
        """The viewer's cache-first community lists (scene_community.py), over the race's own
        client and population; None without a community client."""
        if self._community is None and self.sync is not None and self.rivals is not None:
            from sm64_events.server.scene_community import CommunityGhosts
            self._community = CommunityGhosts(self.sync, self.rivals.population)
        return self._community

    def _definitions(self) -> list[dict]:
        db = self.db
        return db.segment_defs() if db is not None else []

    def target(self, *, course=None, star=None, segment=None) -> Target:
        """The page's target; TargetError when it names none or one that does not exist."""
        if segment is not None:
            if course is not None or star is not None:
                raise TargetError("Name a star or a segment, not both.")
            if not any(row["id"] == segment for row in self._definitions()):
                raise TargetError("No segment by that number.")
            return Target("segment", segment_id=segment)
        if course is None or star is None:
            raise TargetError("Name a course and a star, or a segment.")
        if not 0 <= course <= 25 or not 0 <= star < star_count(course):
            raise TargetError("No such star.")
        return Target("star", course_id=course, star_id=star)

    def active_strategy(self, target: Target) -> str | None:
        """The strategy he last chose for this target (the Practice tab's per-target memory)."""
        if target.kind == "segment":
            return (getattr(self.tracker, "strat_by_segment", None) or {}).get(target.segment_id)
        return (getattr(self.tracker, "strat_by_star", None) or {}).get((target.course_id, target.star_id))

    def describe(self, target: Target, strat: str | None) -> dict:
        """The interface's target object: its ids and the names the Practice log shows."""
        if target.kind == "star":
            names = {"course_name": course_name(target.course_id),
                     "star_name": star_name(target.course_id, target.star_id)}
        else:
            row = next((row for row in self._definitions() if row["id"] == target.segment_id), None)
            names = {"course_name": None, "star_name": None,
                     "segment_name": row["name"] if row else f"Segment {target.segment_id}"}
        return {"course_id": target.course_id, "star_id": target.star_id, "segment_id": target.segment_id,
                "strat": strat, **names, "strat_name": strat}

    def _version(self) -> str:
        """The ROM ghosts are judged for: what grading resolves to (the mode setting, else the
        running cartridge, else US), `TrackerService.effective_game_version`."""
        effective = getattr(self.tracker, "effective_game_version", None)
        try:
            version = effective() if callable(effective) else None
        except Exception:  # noqa: BLE001 - an unreadable mode setting still lists ghosts on US
            version = None
        return version if version in ("us", "jp") else "us"

    def _clock(self, target: Target, attempt=None) -> str:
        """The clock the target is timed on: the rank standards' (`clock_for`), else the Ghost
        Library's rule (segments on real time, stars on in-game time when they have it)."""
        ranks = getattr(self.tracker, "ranks", None)
        if ranks is not None:
            from sm64_events.ranks.standards import entity_key
            return ranks.clock_for(entity_key(target.course_id, target.star_id, target.segment_id))
        if target.kind == "segment" or (attempt is not None and attempt.igt_frames is None):
            return "rta"
        return "igt"

    @staticmethod
    def _frames(attempt, clock: str) -> int | None:
        frames = getattr(attempt, f"{clock}_frames", None)
        return frames if type(frames) is int and frames > 0 else None

    # ------------------------------------------------------------ the stage

    def stage(self, target: Target, strat: str | None = None, *, every: bool = False) -> dict:
        """The stage for this target and strategy and every attempt of it that has a scene,
        fastest finished first, then the newest of the rest. `every`: of any strategy (a Library
        ghost of a strategy he has no scene of still has a stage), named by the chosen one's."""
        from sm64_events.ghost.library_bests import _eligible
        strat = None if every else strat or self.active_strategy(target)
        clock = self._clock(target)
        scenes, db, attempts = self.scenes_of(), self.db, []
        if scenes is not None and db is not None:
            for attempt_id in scenes.ready_ids():
                attempt = db.attempt(attempt_id)
                if attempt is not None and target.owns(attempt) and (strat is None or attempt.strat_tag == strat):
                    attempts.append(attempt)

        def timed(attempt):
            return self._frames(attempt, clock) if attempt.outcome == "success" else None
        attempts.sort(key=lambda attempt: (not _eligible(attempt), timed(attempt) is None, timed(attempt) or 0,
                                           -_instant(attempt.ended_utc)))
        rows = [{"attempt": attempt.id, "time": bare_time(timed(attempt)), "recorded_utc": attempt.started_utc}
                for attempt in attempts]
        if every and attempts:
            strat = attempts[0].strat_tag
        if strat:
            self._warm(target, strat)
        chosen = rows[0] if rows else {"attempt": None, "time": None}
        reason = None if rows else (getattr(scenes, "problem", None) or NO_SCENE)
        return {"target": self.describe(target, strat), "attempt": chosen["attempt"], "time": chosen["time"],
                "reason": reason, "scenes": rows}

    def heading(self, attempt_id: int | None) -> dict:
        """The open answer's "title" and "target": a lab package is "Lab recording"; an attempt
        scene reads as the Practice log names it, with its strategy and time when it has them."""
        db = self.db
        attempt = db.attempt(attempt_id) if attempt_id is not None and db is not None else None
        if attempt is None:
            return {"title": "Lab recording" if attempt_id is None else f"Attempt {attempt_id}", "target": None}
        target = Target.of(attempt)
        described = self.describe(target, attempt.strat_tag)
        time = bare_time(self._frames(attempt, self._clock(target, attempt))) if attempt.outcome == "success" else None
        title = (described["segment_name"] if target.kind == "segment"
                 else f"{described['course_name']} · {described['star_name']}")   # sharing_targets.describe's form
        for part in (attempt.strat_tag, time):
            title = f"{title} · {part}" if part else title       # a display line, never a strategy name
        return {"title": title, "target": described}

    # ------------------------------------------------------------ his runs

    def _candidates(self, attempt_id: int) -> list[Path]:
        """Where `ReplayService._find_native` and `native_with_inputs` keep one run's poses: the
        committed clip, a saved video's copy and the playback copy (a cache key only)."""
        root = Path(self.replay.cfg.save_root)
        paths = [root / "native" / f"attempt_{attempt_id}.ghost", root / "native" / f"attempt_{attempt_id}.json",
                 root / "native" / "inputs" / f"attempt_{attempt_id}.ghost"]
        saved = self.replay.find_saved(attempt_id) if hasattr(self.replay, "find_saved") else None
        if saved is not None:
            paths += [Path(saved).with_suffix(".ghost"), Path(saved).with_suffix(".json")]
        return paths

    def _files(self, attempt_id: int) -> tuple:
        """(name, size, mtime) of each of `_candidates`; size None where there is no file."""
        files = []
        for path in self._candidates(attempt_id):
            try:
                stat = path.stat()
                files.append((path.name, stat.st_size, stat.st_mtime_ns))
            except OSError:
                files.append((path.name, None, None))
        return tuple(files)

    @staticmethod
    def _stamp(attempt, files: tuple) -> tuple:
        return tuple(getattr(attempt, name, None) for name in (
            "session_id", "strat_tag", "native_clock_frame", "native_clock_utc", "native_clock_kind",
            "closed_frame", "ended_utc", "outcome")) + files

    def run_clip(self, attempt, files: tuple | None = None):
        """(path, manifest) of the copy the in-game race would play for this run, or the plain
        sentence why it is not a ghost (no committed poses, invalid, or inputs not all known).
        Cached until the run or one of its files changes, as the native session caches it.
        `files` is `_files(attempt.id)` when the caller has just read it."""
        from sm64_events.ghost.clip import INCOMPLETE_INPUTS
        stamp = self._stamp(attempt, self._files(attempt.id) if files is None else files)
        with self._lock:
            cached = self._runs.get(attempt.id)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        try:
            found = self.replay.find_native(attempt.id)
            if found is None:
                result = NOT_SAVED
            else:
                result = self.replay.native_with_inputs(attempt.id, found)
                if result[1].get("inputs_complete") is not True:
                    result = INCOMPLETE_INPUTS
        except (LookupError, OSError, ValueError, RuntimeError) as error:
            result = f"This run's recording cannot be read: {error}"
        with self._lock:                                     # the playback copy may be new
            self._runs[attempt.id] = (self._stamp(attempt, self._files(attempt.id)), result)
            while len(self._runs) > 4096:
                self._runs.pop(next(iter(self._runs)))
        return result

    @staticmethod
    def _has_poses(files: tuple) -> bool:
        """Whether a committed clip exists at all: a refused one is an exclusion, none is not."""
        return any(name.endswith(".ghost") and size is not None for name, size, _ in files)

    def _run_attempts(self, target: Target, strat: str) -> list:
        db = self.db
        if db is None:
            return []
        lookup = getattr(db, "target_attempts", None)
        attempts = (lookup(course_id=target.course_id, star_id=target.star_id, segment_id=target.segment_id)
                    if lookup is not None else [attempt for attempt in db.attempts() if target.owns(attempt)])
        return [attempt for attempt in attempts if attempt.strat_tag == strat]

    # ------------------------------------------------------------ the catalog

    def _ref(self, source: tuple) -> str:
        return "g" + hmac.new(self._key, repr(source).encode("utf-8"), hashlib.sha256).hexdigest()[:20]

    def _context(self, target: Target, strat: str) -> SimpleNamespace:
        """The rival race's identity for this target and strategy (its clock, ROM and region),
        the population, his player ids and the name he goes by."""
        version, rivals, identity = self._version(), self.rivals, None
        if rivals is not None:
            try:
                identity = rivals.identity_for({"kind": target.kind, "entity_key": target.entity_key, "strat": strat},
                                               version=version, scope="strategy")
            except (LookupError, ValueError, TypeError):
                identity = None
        db = self.db
        from sm64_events.ghost.library_preferences import AUTHOR_KEY
        name = db.get_state(AUTHOR_KEY, None) if db is not None and hasattr(db, "get_state") else None
        return SimpleNamespace(
            version=version, identity=identity, population=getattr(rivals, "population", None),
            owned=rivals.owned_players() if rivals is not None else frozenset(),
            clock=identity.clock if identity is not None else self._clock(target),
            you=name.strip() if isinstance(name, str) and name.strip() else "You")

    def catalog(self, target: Target, strat: str, context=None) -> tuple[list[Ghost], dict]:
        """Every ghost for this target and strategy, and the exclusion counts: {"refused": his
        runs and imports whose recording is not a perfect ghost, "unreadable": listed ghosts
        whose recording then failed to load}."""
        context = context or self._context(target, strat)
        target_row = {"kind": target.kind, "entity_key": target.entity_key, "strat": strat, "scope": "strategy"}
        counts, runs = {"refused": 0, "unreadable": 0}, {"lineages": set(), "clips": set()}
        offers = [*self._run_offers(target, strat, context, counts, runs),
                  *self._population_offers(strat, context, runs),
                  *self._community_offers(context, runs),
                  *self._import_offers(target, strat, context, counts)]
        ghosts, seen = [], set()
        for source, player, who, frames, recorded, yours, attempt, version in offers:
            ref = self._ref(source)
            if ref in self._refused:
                counts["unreadable"] += 1
            elif source not in seen:               # a race-held list and a viewer-held page can share a row
                seen.add(source)
                ghosts.append(Ghost(ref, source, player, who, frames, recorded, yours, target_row, attempt,
                                    region(version)))
        with self._lock:
            self._refs.update((ghost.ref, ghost) for ghost in ghosts)
            if self._exclusions.get((target, strat)) != counts and any(counts.values()):
                log.info("The scene ghosts of %s %s left out %s.", target.entity_key, strat, counts)
            self._exclusions[(target, strat)] = counts
        return ghosts, counts

    def _run_offers(self, target: Target, strat: str, context, counts: dict, runs: dict):
        """His journal runs the race would play: (source, player, who, frames, recorded, yours,
        attempt, version). `runs` collects their lineages and playback-copy hashes, so a population or
        community copy of the same run is not listed twice."""
        from sm64_events.ghost.library_bests import _eligible
        if self.replay is None:
            return
        for attempt in self._run_attempts(target, strat):
            frames = self._frames(attempt, context.clock)
            if not _eligible(attempt) or frames is None:
                continue
            files = self._files(attempt.id)
            if not self._has_poses(files):
                continue
            clip = self.run_clip(attempt, files)
            if isinstance(clip, str):
                counts["refused"] += 1
                continue
            runs["lineages"].add((attempt.session_id, attempt.id))
            runs["clips"].add(clip[1].get("sha256"))
            yield (("attempt", attempt.id), context.you, YOU, frames, attempt.started_utc, True, attempt.id,
                   getattr(attempt, "game_version", None))

    def _same_run(self, row, runs: dict) -> bool:
        """Whether a population or community row is a copy of a run listed from the journal."""
        source = row.get("source")
        return (isinstance(source, dict) and (source.get("session_id"), source.get("attempt_id")) in runs["lineages"]
                or (row.get("sha256") or row.get("raw_sha256")) in runs["clips"])

    def _his_attempt(self, row) -> int | None:
        """The journal attempt a certified copy of his came from, when this journal still has it."""
        source, db = row.get("source"), self.db
        if not isinstance(source, dict) or db is None or type(source.get("attempt_id")) is not int:
            return None
        attempt = db.attempt(source["attempt_id"])
        same = attempt is not None and (attempt.session_id, attempt.started_utc) == (
            source.get("session_id"), source.get("started_utc"))
        return attempt.id if same else None

    def _population_offers(self, strat: str, context, runs: dict):
        """The rival population's recordings; his copy of a run listed above is left out."""
        if context.population is None or context.identity is None:
            return
        for row in self._population_rows(context.population, context.identity, strat):
            frames = row.get("frames")
            if type(frames) is not int or frames <= 0 or self._same_run(row, runs):
                continue
            mine = row.get("player_id") in context.owned
            player = context.you if mine else str(row.get("player_name") or "Player")
            who = YOU if mine else ("player", row.get("player_id") or row["id"])
            yield (("population", row["id"]), player, who, frames, row.get("recorded_utc"), mine,
                   self._his_attempt(row) if mine else None,
                   row["recorded_game_version"] if "recorded_game_version" in row else row.get("game_version"))

    def _community_offers(self, context, runs: dict):
        """Community rows the viewer holds for this identity (scene_community.py; never a call)."""
        community = self.community
        if community is None or context.identity is None:
            return
        for row in community.rows(context.identity):
            if type(row.get("frames")) is not int or row["frames"] <= 0 or self._same_run(row, runs):
                continue
            mine = row.get("player_id") in context.owned
            player = context.you if mine else str(row.get("player_name") or "Player")
            who = YOU if mine else ("player", row.get("player_id") or row["id"])
            yield ("population", row["id"]), player, who, row["frames"], None, mine, None, row.get("game_version")

    def _import_offers(self, target: Target, strat: str, context, counts: dict):
        """Imported ghosts of this target, strategy and clock whose recording is whole."""
        sharing = self._sharing()
        for row in sharing.imported_rows() if sharing is not None else ():
            if ((row.get("kind"), row.get("entity_key"), row.get("strat"), row.get("clock"))
                    != (target.kind, target.entity_key, strat, context.clock)):
                continue
            if not row.get("available"):
                counts["refused"] += 1
            elif type(row.get("frames")) is int and row["frames"] > 0:
                author = str(row.get("author") or "Player")
                who = ("import", _folded(author))
                yield (("import", row["import_id"]), author, who, row["frames"], row.get("recorded_utc"), False, None,
                       row.get("game_version"))

    def _population_rows(self, population, identity, strat: str) -> tuple:
        """The population's recordings for this identity, kept until its `generation` moves:
        a population row binds its target against the segment definitions, which every row
        reads from the journal again (~5 ms a row on his data)."""
        from sm64_events.ranks.region_scope import servable_versions
        versions = servable_versions(identity.region_scope, identity.game_version)
        key = (identity.kind, identity.entity_key, identity.clock, identity.game_version, strat, versions)
        generation = getattr(population, "generation", None)
        with self._lock:
            cached = self._snapshots.get(key)
        if cached is not None and generation is not None and cached[0] == generation:
            return cached[1]
        rows = tuple(population.metadata_snapshot(identity.kind, identity.entity_key, clock=identity.clock,
                                                  game_version=identity.game_version, strategy=strat,
                                                  versions=versions))
        with self._lock:
            self._snapshots[key] = (generation, rows)
            while len(self._snapshots) > 256:
                self._snapshots.pop(next(iter(self._snapshots)))
        return rows

    def _sharing(self):
        if self.replay is None or self.db is None:
            return None
        from sm64_events.ghost.sharing import sharing_for
        try:
            return sharing_for(self.replay, self.db)
        except (RuntimeError, ValueError, OSError):
            return None

    def _warm(self, target: Target, strat: str) -> None:
        """Checks this target's runs on a thread of its own, so the ghost list a page asks for
        next answers from the cache (the first read of a clip hashes and decodes all of it)."""
        if self.replay is None or self.db is None:
            return
        with self._lock:
            if (target, strat) in self._warming:
                return
            self._warming.add((target, strat))

        def work():
            try:
                self.catalog(target, strat)
            except Exception:  # noqa: BLE001 - the page's own request repeats it and answers
                log.debug("Warming the scene ghosts failed.", exc_info=True)
            finally:
                with self._lock:
                    self._warming.discard((target, strat))
        threading.Thread(target=work, name="scene-ghosts-warm", daemon=True).start()

    def exclusions(self) -> dict:
        """The last listing's exclusion counts per target and strategy (a receipt, not the page)."""
        with self._lock:
            return {f"{target.entity_key} {strat}": dict(counts) for (target, strat), counts in self._exclusions.items()}

    # ------------------------------------------------------------ the sections

    def strategies(self, target: Target) -> list[str]:
        """The strategies Top ghosts' All strategies reads, first seen first: the target's list as
        the stage picker and the Practice card show it (tracking/views.build_entity_strategies), then
        any other his runs or imported ghosts of this target name (his active strategy first). The
        community lists only answer a named strategy, so a strategy no one here has named is not
        asked for."""
        names = [self.active_strategy(target)]
        db = self.db
        if db is not None and self.tracker is not None:
            from sm64_events.tracking.views import build_entity_strategies
            key = (f"segment:{target.segment_id}" if target.kind == "segment"
                   else f"star:{target.course_id}:{target.star_id}")
            try:
                names += [row.get("name") for row in build_entity_strategies(db, self.tracker, key).get("strategies", ())]
            except (LookupError, AttributeError, TypeError, ValueError):
                log.debug("The scene ghosts read no strategy list for %s.", key, exc_info=True)
            lookup = getattr(db, "target_attempts", None)
            if lookup is not None:
                names += [attempt.strat_tag for attempt in lookup(course_id=target.course_id, star_id=target.star_id,
                                                                  segment_id=target.segment_id)]
        sharing = self._sharing()
        names += [row.get("strat") for row in (sharing.imported_rows() if sharing is not None else ())
                  if (row.get("kind"), row.get("entity_key")) == (target.kind, target.entity_key)]
        return list(dict.fromkeys(name for name in names if isinstance(name, str) and name))

    def _every_context(self, target: Target) -> dict:
        """{strategy: its race context} for All strategies, on the target's clock only: a ghost
        timed on another clock is not a time the list can rank beside these."""
        clock, contexts = self._clock(target), {}
        for name in self.strategies(target):
            context = self._context(target, name)
            if context.clock == clock:
                contexts[name] = context
        return contexts

    def every_top(self, target: Target, *, page: int = 1) -> dict:
        """Top ghosts across every strategy: each player's one best ghost of this target whatever
        its strategy, fastest first; rank is its place among those bests, and each row names its
        strategy ("strat") and is graded on that strategy's ladder."""
        if self.replay is None and self.rivals is None:
            return _page([], {}, page, NO_RECORDINGS)
        contexts, everything = self._every_context(target), []
        for name, context in contexts.items():
            everything += self.catalog(target, name, context)[0]
        best = _places(everything)
        leaders = {}
        for ghost in sorted(everything, key=_fastest):
            leaders.setdefault(ghost.who, ghost)
        community = self.community
        held = [context.identity for context in contexts.values()] if community is not None else []
        note = next((said for said in (community.note(identity) for identity in held) if said), None)
        more_pages = any(community.incomplete(identity) for identity in held)

        def tiers(shown):
            found = {}
            for name, context in contexts.items():
                mine = [ghost for ghost in shown if ghost.target["strat"] == name]
                if mine:
                    found.update(self._tiers(context, mine))
            return found
        return _page(list(leaders.values()), best, page, note or NO_GHOSTS_ANY, tiers=tiers, note=note,
                     more_pages=more_pages)

    def ghosts(self, target: Target, strat: str | None = None, *, section: str, page: int = 1,
               sort: str = "fastest", query: str = "", player: str = "", every: bool = False) -> dict:
        """One page (PAGE_SIZE rows) of a section: "yours" (his, fastest or newest first), "top"
        (each player's best, fastest first; `every`: across all strategies, `every_top`), "search"
        (the best of each player whose name holds `query`) or "player" (one player's ghosts,
        fastest first)."""
        if section not in SECTIONS:
            raise TargetError(f"Choose a section: {', '.join(SECTIONS)}.")
        if sort not in SORTS:
            raise TargetError("Sort by fastest or newest.")
        if every:
            if section != "top":
                raise TargetError("Only Top ghosts lists every strategy.")
            return self.every_top(target, page=page)
        strat = strat or self.active_strategy(target)
        if not strat:
            return _page([], {}, page, NO_STRATEGY)
        if self.replay is None and self.rivals is None:
            return _page([], {}, page, NO_RECORDINGS)
        context = self._context(target, strat)
        everything, _counts = self.catalog(target, strat, context)
        best = _places(everything)
        owned = {}
        for ghost in everything:
            owned[ghost.who] = owned.get(ghost.who, 0) + 1
        tiers = lambda shown: self._tiers(context, shown)               # noqa: E731
        community = self.community if section != "yours" else None
        note = community.note(context.identity) if community is not None else None
        if section == "yours":
            rows = [ghost for ghost in everything if ghost.yours]
            rows.sort(key=(lambda ghost: (-_instant(ghost.recorded_utc), ghost.frames, ghost.ref))
                      if sort == "newest" else _fastest)
            return _page(rows, best, page, NO_YOURS, tiers=tiers)
        if section == "player":
            wanted = _folded(player)
            rows = sorted((ghost for ghost in everything if wanted and _folded(ghost.player) == wanted), key=_fastest)
            return _page(rows, best, page, note or NO_PLAYER, tiers=tiers, note=note)
        leaders = {}
        for ghost in sorted(everything, key=_fastest):
            leaders.setdefault(ghost.who, ghost)
        rows = list(leaders.values())
        if section == "search":
            wanted = _folded(query)
            if not wanted:
                return _page([], best, page, NO_QUERY)
            rows = [ghost for ghost in rows if wanted in _folded(ghost.player)]
            return _page(rows, best, page, note or NO_MATCH, tiers=tiers, more=owned, note=note)
        # A community list that continues past what is held offers one more page; asking for it fetches it.
        more_pages = community is not None and community.incomplete(context.identity)
        return _page(rows, best, page, note or NO_GHOSTS, tiers=tiers, more=owned, note=note, more_pages=more_pages)

    async def fill(self, target: Target, strat: str | None = None, *, section: str, page: int = 1,
                   query: str = "", player: str = "", every: bool = False) -> int:
        """Before a /ghosts page is read: fetch what its section needs from the community that
        is not held yet (scene_community.py). On the event loop the client runs on; returns the
        network calls it made, 0 inside the publication lease or without a community. All
        strategies (`every`) fills each strategy's top list the same way, all at once."""
        community = self.community
        if every:
            if community is None or section != "top":
                return 0
            contexts = await asyncio.to_thread(self._every_context, target)
            calls = await asyncio.gather(*(community.fill(context.identity, section="top",
                                                          rows_wanted=max(1, page) * PAGE_SIZE)
                                           for context in contexts.values()))
            return sum(calls)
        strat = strat or self.active_strategy(target)
        if community is None or not strat or section not in ("top", "search", "player"):
            return 0
        context = await asyncio.to_thread(self._context, target, strat)
        name = query if section == "search" else player
        return await community.fill(context.identity, section=section, rows_wanted=max(1, page) * PAGE_SIZE,
                                    name=name)

    # ------------------------------------------------------------ one recording by id (the Library's ghost button)

    def _held(self, identifier):
        """The population's row for a recording id (a community row inside its lease, or a local
        one), or None; ValueError for an id that is no recording id."""
        population = getattr(self.rivals, "population", None)
        if population is None:
            return None
        try:
            return population.get(identifier)
        except LookupError:
            return None

    async def fill_recording(self, identifier) -> int:
        """Before `recording_item`: a community recording whose held row lapsed (the Library read it
        more than the lease ago) is read again with the race's client (`recording_metadata`, one
        call); 0 when it is held, local, or there is no community."""
        community = self.community
        try:
            if community is None or await asyncio.to_thread(self._held, identifier) is not None:
                return 0
        except ValueError:
            return 0
        if not community.active():
            return 0
        from sm64_events.community.config import CommunityError
        try:
            await self.sync.recording_metadata(identifier)
        except CommunityError as error:
            log.info("The scene viewer could not read ghost %s again: %s", identifier, error.code)
        return 1

    def recording_item(self, target: Target, identifier) -> dict:
        """One recording of the rival population by its id, as a row of this target's list
        ({"item"}, its rank among its strategy's ghosts), so the page can add it and load_ghost can
        play it like any listed ghost; or {"item": None, "reason"}. The Library's ghost button
        names it; the row is the one the Library's page left with the race (never a call here)."""
        try:
            row = self._held(identifier)
        except ValueError:
            row = None
        if row is None:
            return {"item": None, "reason": NOT_HELD if self.rivals is not None else NO_RECORDINGS}
        strat, frames = row.get("strategy"), row.get("frames")
        if (row.get("kind"), row.get("entity_key")) != (target.kind, target.entity_key):
            return {"item": None, "reason": OTHER_TARGET}
        if not isinstance(strat, str) or not strat or type(frames) is not int or frames <= 0:
            return {"item": None, "reason": NOT_HELD}
        context = self._context(target, strat)
        listed, _counts = self.catalog(target, strat, context)
        source = ("population", row["id"])
        ghost = next((ghost for ghost in listed if ghost.source == source), None)
        if ghost is None:
            mine = row.get("player_id") in context.owned
            ghost = Ghost(self._ref(source), source, context.you if mine else str(row.get("player_name") or "Player"),
                          YOU if mine else ("player", row.get("player_id") or row["id"]), frames,
                          row.get("recorded_utc"), mine,
                          {"kind": target.kind, "entity_key": target.entity_key, "strat": strat, "scope": "strategy"},
                          None, region(row["recorded_game_version"] if "recorded_game_version" in row
                                       else row.get("game_version")))
            with self._lock:
                refused = self._refused.get(ghost.ref)
                if refused is None:
                    self._refs[ghost.ref] = ghost
            if refused is not None:
                return {"item": None, "reason": refused}
            listed = [*listed, ghost]
        page = _page([ghost], _places(listed), 1, None, tiers=lambda shown: self._tiers(context, shown))
        return {"item": page["items"][0]}

    def _tiers(self, context, ghosts) -> dict:
        """Each shown ghost's rank on the strategy's ladder (the Lineup's rank cap)."""
        ranks = getattr(self.tracker, "ranks", None)
        if ranks is None or context.identity is None:
            return {}
        from contextlib import nullcontext

        from sm64_events.ghost.adaptive import rank_frames
        found = {}
        # One pinned read for the page: each rank_frames outside one copies the whole standards.
        with ranks.read_context() if hasattr(ranks, "read_context") else nullcontext():
            for ghost in ghosts:
                graded = rank_frames(ranks, context.identity, ghost.frames)
                if graded:
                    found[ghost.ref] = {"tier": graded["tier"], "division": graded["division"]}
        return found

    # ------------------------------------------------------------ the renderer's recordings

    def load_ghost(self, ref) -> dict:
        """A listed ghost's recording as the in-game ghost plays it: {"clip": the bytes of the
        native clip file the race would hand the renderer, checked against the hash the race
        validated and decoded whole, "player", "frames" (its records), "time" (the run time the list
        shows, 11"03, or None), "label" (the in-game
        name box, `native_session.ghost_label`), "target" (the listed row's {kind, entity_key, strat,
        scope}: a re-simulation takes the star's act from it)}. Blocking (it reads and decodes the whole clip;
        call it off the event loop); LookupError with a plain sentence."""
        from sm64_events.ghost.clip import INCOMPLETE_INPUTS, ClipError, decode_clip, inputs_complete
        with self._lock:
            ghost = self._refs.get(ref) if isinstance(ref, str) else None
        if ghost is None:
            raise LookupError(UNLISTED)
        try:
            path, manifest, label = self._recording(ghost)
            with Path(path).open("rb") as stream:
                data = stream.read(MAX_CLIP_BYTES + 1)
            if len(data) > MAX_CLIP_BYTES:
                raise ClipError("This ghost recording is larger than any real one.")
            digest = manifest.get("sha256")
            if isinstance(digest, str) and hashlib.sha256(data).hexdigest() != digest:
                raise ClipError("This ghost recording changed after it was checked; pick it again.")
            clip = decode_clip(data)
            if not inputs_complete(clip):
                raise ClipError(INCOMPLETE_INPUTS)
        except (OSError, ValueError, LookupError) as error:
            reason = str(error) or "This ghost recording cannot be read."
            with self._lock:
                self._refused[ref] = reason
            raise LookupError(reason) from None
        return {"clip": data, "player": ghost.player, "frames": len(clip.frames), "label": label,
                "time": bare_time(ghost.frames), "target": dict(ghost.target)}

    def _recording(self, ghost: Ghost) -> tuple:
        """(path, manifest, label) through the race's own door for this ghost's source."""
        from sm64_events.ghost.native_session import ghost_label
        kind, identifier = ghost.source
        if kind == "attempt":
            if self.replay is None:
                raise LookupError(NO_RECORDINGS)
            attempt = self.replay.attempt(identifier)
            found = self.run_clip(attempt)
            if isinstance(found, str):
                raise LookupError(found)
            return (*found, ghost_label(SimpleNamespace(pb=self._is_pb(attempt, ghost.target), import_id=None)))
        if kind == "population":
            if self.rivals is None:
                raise LookupError(NO_RECORDINGS)
            population = self.rivals.population
            if self.community is not None and not population.is_ready(identifier):
                self.community.download(identifier)        # into the race's own cache, once
            portable = population.get(identifier)["ghost"]
            path, manifest = population.resolve(portable, ghost.target)
        else:
            sharing = self._sharing()
            if sharing is None:
                raise LookupError(NO_RECORDINGS)
            portable = sharing.get(identifier)
            path, manifest = sharing.resolve(portable, ghost.target)
        # His own certified copy is his run (YOUR RUN); anyone else's shows their credited name.
        return path, manifest, ghost_label(SimpleNamespace(pb=False, import_id=None) if ghost.yours else portable)

    def _is_pb(self, attempt, target: dict) -> bool:
        """Whether this run is his current PB ghost for the strategy (its name box: YOUR PB)."""
        from sm64_events.ghost.pb import current_pb_ghost
        if self.db is None:
            return False
        try:
            pb = current_pb_ghost(self.db, target["kind"], target["entity_key"], target["strat"],
                                  running=self._version(), ranks=getattr(self.tracker, "ranks", None))
        except (LookupError, ValueError, TypeError):
            return False
        return pb is not None and pb.attempt_id == attempt.id


def _page(rows, best, page, empty_reason, *, tiers=lambda shown: {}, more=None, note=None,
          more_pages=False) -> dict:
    """One page of `rows` (1-based): the interface's items, page, pages, a reason when it is
    empty, and `note`, why other players' ghosts may be missing (community unreachable)."""
    pages = math.ceil(len(rows) / PAGE_SIZE) + (1 if more_pages else 0)
    page = min(max(1, int(page)), max(1, pages))
    shown = rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    graded = tiers(shown)
    items = [{"ref": ghost.ref, "player": ghost.player, "time": bare_time(ghost.frames), "frames": ghost.frames,
              "rank": _rank(ghost, best), "recorded_utc": ghost.recorded_utc,
              "more": max(0, more.get(ghost.who, 1) - 1) if more is not None else 0, "yours": ghost.yours,
              "attempt": ghost.attempt, "strat": ghost.target.get("strat"), "version": ghost.version,
              **graded.get(ghost.ref, {"tier": None, "division": None})}
             for ghost in shown]
    return {"items": items, "page": page, "pages": pages, "reason": None if items else empty_reason, "note": note}

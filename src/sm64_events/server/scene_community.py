"""The Scene viewer's community ghosts, cache first (M4 round 4; lab only, read by scene_ghosts.py).

Griffin, 2026-10-27: "We should also consider how caching / cloudflare database interactions work
here, to make sure we're being as cost effective as possible (and, if someone loads a ghost here,
could we simply reuse that ghost locally if they go to actually challenge it later, for example)."

Every call goes through the community client the race already uses (`community/sync.py`
CommunitySync, over the signed-in account): no new route, key, HTTP client or store. Each Worker
call spends one ticket of the account's 500 a day, cache hits on the service included, and its
listings answer `Cache-Control: no-store` with no conditional request; recordings are immutable
by id. So the saving is in not asking (docs/community-protocol.md, "The Scene viewer's reads"):

- The race's own list first: a complete rival pool the race holds for this identity (inside its
  lease) answers the top list and every search, with no call of the viewer's.
- Top: one `GET /v1/library/recordings` page (20 rows, fastest first) serves two viewer pages;
  the next page is asked only when the viewer pages past what is held, at most MAX_PAGES_PER_ASK
  a request. A list is held for `cache.TTL` (600 s, the publication lease every community row
  already carries), so reopening a target inside it costs nothing. A strategy the catalog does not
  list (`CommunitySync.catalog_lists`: the race's hourly `/v1/catalog`) is never asked: the service
  would refuse it. All strategies (Top ghosts' first choice, `SceneGhosts.every_top`) fills every
  strategy's list this way at once, so a target's first look costs one page per listed strategy.
- Search and one player's ghosts: answered from what is held when the held top list is complete,
  or a held search for a shorter name already was; otherwise one `GET /v1/search` (20 rows) per
  name, held for the same lease. The page asks when a search is submitted, never per keystroke.
- A refusal (signed out, an unsupported strategy, offline) is held for FAILURE_S; the client's own
  cooldown covers 429s and a spent daily budget. The lists then answer from this Trainer's ghosts.
- Recordings: listed rows are the population's own (`renew_metadata`, `remember_page`), so a
  listed ghost resolves through `population.resolve` exactly like a race rival. One not yet
  downloaded is asked for with `CommunitySync.request_assets`, the race's downloader (two calls:
  its publication gate and its bytes), into the same validated, content-addressed cache
  (`data/community_cache/<id>.ghost`, `community/cache.py`). Racing it later finds it there with no
  call, and a ghost a race already downloaded loads here with none. That cache is memory-indexed
  and cleared at each start by design (stale private bytes never become an offline roster), so
  the reuse lasts while this Trainer runs.
"""
from __future__ import annotations

import asyncio
import threading
import time
import unicodedata
from dataclasses import dataclass, field

from sm64_events.community.cache import TTL
from sm64_events.community.config import CommunityError

FAILURE_S = 60.0              # a refusal the client does not cool down itself is not asked again for this long
MAX_PAGES_PER_ASK = 2         # library pages one viewer request may fetch (20 rows each)
ASSET_WAIT_S = 60.0           # how long load_ghost waits for the race's downloader
ASSET_POLL_S = 0.1
UNAVAILABLE = "Other players' ghosts are unavailable right now; these are the ones this Trainer has."
SIGN_IN = "Sign in to see other players' ghosts."
NO_DOWNLOAD = "This ghost could not be downloaded; try again later."
STILL_DOWNLOADING = "This ghost is still downloading; add it again in a moment."


def folded(name) -> str:
    """A name as the service compares names (NFKC, case-folded, trimmed)."""
    return unicodedata.normalize("NFKC", name).casefold().strip() if isinstance(name, str) else ""


@dataclass
class Shelf:
    """One held answer: the service's rows in its order, where it continues, and its lease."""
    rows: list = field(default_factory=list)
    cursor: str | None = None
    complete: bool = False
    until: float = 0.0


class CommunityGhosts:
    """The viewer's held community lists for each identity; `sync` is the race's CommunitySync and
    `population` its CachedPopulation. `fill` (async, on the loop the client runs on) is the only
    method that may reach the network; `rows`, `note` and `download` are for worker threads."""

    def __init__(self, sync, population, *, clock=time.monotonic):
        self.sync, self.population, self.clock = sync, population, clock
        self._lock = threading.Lock()
        self.lists: dict[tuple, Shelf] = {}          # identity key -> the top list
        self.searches: dict[tuple, Shelf] = {}       # (identity key, folded name) -> a search
        self.failed: dict[tuple, tuple[float, str]] = {}   # identity key -> (until, the sentence)
        self._asking: dict[tuple, asyncio.Lock] = {}

    def active(self) -> bool:
        snapshot = self.sync.account.snapshot() if self.sync is not None else {}
        return snapshot.get("status") == "active" and bool(snapshot.get("uid"))

    def _key(self, identity) -> tuple | None:
        try:
            return self.population.identity_key(identity)
        except (CommunityError, ValueError, TypeError):
            return None

    # ------------------------------------------------------------ the loop's side (network)

    async def fill(self, identity, *, section: str, rows_wanted: int, name: str = "") -> int:
        """Makes sure what this page needs is held; returns the network calls it made (0 inside
        a lease). A failure is remembered, never raised: the lists answer without it."""
        key = self._key(identity) if identity is not None else None
        if key is None or self.sync is None or section not in ("top", "search", "player"):
            return 0
        if not self.active():
            self._fail(key, SIGN_IN)
            return 0
        with self._lock:
            failed = self.failed.get(key)
        if failed is not None and failed[0] > self.clock():
            return 0
        lock = self._asking.setdefault(key, asyncio.Lock())
        async with lock:                                     # one ask per identity at a time
            try:
                if section == "top":
                    return await self._fill_list(identity, key, rows_wanted)
                return await self._fill_search(identity, key, folded(name)) if folded(name) else 0
            except CommunityError as error:
                self._fail(key, SIGN_IN if error.code == "sign_in_required" else UNAVAILABLE,
                           error.retry_after)
                return 0

    def _fail(self, key, sentence: str, retry_after=None) -> None:
        with self._lock:
            self.failed[key] = (self.clock() + max(FAILURE_S, float(retry_after or 0)), sentence)

    async def _library_page(self, identity, cursor):
        target = self.population.portable_target(identity)
        return await self.sync.library_recordings(target, identity.clock, identity.game_version,
                                                  identity.strategy, cursor)

    def _race_holds(self, identity) -> bool:
        """Whether the race already holds this identity's whole rival list (a complete pool inside
        its lease, `CommunityRivals.request`): its rows reach the viewer through the population."""
        try:
            return bool(self.population.coverage(identity).get("complete"))
        except (CommunityError, ValueError, TypeError, AttributeError):
            return False

    async def _fill_list(self, identity, key, rows_wanted: int) -> int:
        if self._race_holds(identity):
            return 0
        now = self.clock()
        with self._lock:
            held = self.lists.get(key)
        if held is None or held.until <= now:
            if not await self.sync.catalog_lists(identity):      # a strategy no one can share: nothing to ask
                return 0
            shelf = Shelf(until=now + TTL)
        elif held.complete or len(held.rows) >= rows_wanted:
            return 0
        else:                                    # a copy: readers on other threads keep the held one
            shelf = Shelf(list(held.rows), held.cursor, held.complete, held.until)
        cursor, calls = shelf.cursor, 0
        while calls < MAX_PAGES_PER_ASK and (calls == 0 or len(shelf.rows) < rows_wanted) and not shelf.complete:
            try:
                page = await self._library_page(identity, cursor)
            except CommunityError as error:
                if error.code != "invalid_cursor" or cursor is None:     # a continuation expires in 5 min
                    raise
                shelf, cursor = Shelf(until=self.clock() + TTL), None
                page = await self._library_page(identity, None)
            calls += 1
            seen = {row["id"] for row in shelf.rows}
            shelf.rows += [row for row in page["recordings"] if row["id"] not in seen]
            shelf.cursor, shelf.complete = page["next_cursor"], bool(page["complete"])
            cursor = shelf.cursor
            if len(shelf.rows) >= rows_wanted:
                break
        with self._lock:
            self.lists[key] = shelf
            self.failed.pop(key, None)
        return calls

    def _answered(self, key, name: str, now: float) -> bool:
        """Whether held answers already cover every player whose name holds `name`."""
        with self._lock:
            listed = self.lists.get(key)
            if listed is not None and listed.until > now and listed.complete:
                return True
            return any(held.until > now and held.complete and name.startswith(prefix)
                       for (identity, prefix), held in self.searches.items() if identity == key)

    async def _fill_search(self, identity, key, name: str) -> int:
        if self._race_holds(identity) or self._answered(key, name, self.clock()):
            return 0
        # Its own task: the client cancels an older search when a newer one starts (the practice
        # card's rival search shares it), and that must cost this request its rows, not its life.
        task = asyncio.ensure_future(self.sync.search(identity, name))
        try:
            found = await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
            return 1
        remote = isinstance(found.get("next_cursor"), str)
        rows = [{key_: value for key_, value in row.items() if key_ != "ghost"}
                for row in found.get("records", ()) if row.get("provenance") == "community-unverified"]
        with self._lock:
            self.searches[(key, name)] = Shelf(rows=rows, cursor=None, complete=not remote,
                                               until=self.clock() + TTL)
            while len(self.searches) > 64:
                self.searches.pop(next(iter(self.searches)))
            self.failed.pop(key, None)
        return 1

    # ------------------------------------------------------------ the catalog's side (no network)

    def rows(self, identity) -> list[dict]:
        """Every held community row for this identity (top list, then searches), once each."""
        key = self._key(identity) if identity is not None else None
        if key is None:
            return []
        now, found, seen = self.clock(), [], set()
        with self._lock:
            shelves = [self.lists.get(key)] + [held for (identity_key, _name), held in self.searches.items()
                                               if identity_key == key]
            for shelf in shelves:
                for row in (shelf.rows if shelf is not None and shelf.until > now else ()):
                    if row["id"] not in seen:
                        seen.add(row["id"])
                        found.append(row)
        return found

    def incomplete(self, identity) -> bool:
        """Whether the held top list continues past what is held (the page may ask for more)."""
        key = self._key(identity) if identity is not None else None
        if key is None or self._race_holds(identity):
            return False
        with self._lock:
            shelf = self.lists.get(key)
            return bool(shelf and shelf.until > self.clock() and not shelf.complete)

    def note(self, identity) -> str | None:
        """Why other players' ghosts are missing from a list, or None."""
        key = self._key(identity) if identity is not None else None
        with self._lock:
            failed = self.failed.get(key) if key is not None else None
        return failed[1] if failed is not None and failed[0] > self.clock() else None

    def download(self, identifier: str) -> None:
        """Blocking: once this returns, `population.resolve` serves the recording from the race's
        own cache. Already there, it asks nothing; else the race's downloader fetches it."""
        if self.population.is_ready(identifier):
            return
        if self.sync is None or not self.active():
            raise LookupError(SIGN_IN)
        if not self.sync.request_assets([identifier]):
            raise LookupError(NO_DOWNLOAD)
        deadline = time.monotonic() + ASSET_WAIT_S
        while time.monotonic() < deadline:
            if self.population.is_ready(identifier):
                return
            if identifier in self.sync.refused_assets():
                raise LookupError(NO_DOWNLOAD)
            time.sleep(ASSET_POLL_S)
        raise LookupError(STILL_DOWNLOADING)

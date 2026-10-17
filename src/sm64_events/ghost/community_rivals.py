"""Whole-ladder rival lists and download staging, outside native playback."""
from uuid import UUID

from sm64_events.ghost.adaptive import MAX_RIVALS, player_key
from sm64_events.ghost.community_catalog import match_windows
from sm64_events.ghost.portable import checked_hash


class CommunityRivals:
    def __init__(self, sync, population):
        self.sync, self.population = sync, population
        self.key, self.windows = None, ()
        self._pins_key, self._pins = None, ()
        self.downloading = False

    def owner(self):
        snapshot = self.sync.account.snapshot() if self.sync is not None else {}
        owner = snapshot.get('uid')
        if not snapshot.get('authenticated') or snapshot.get('status') != 'active':
            return None
        try:
            return owner if str(UUID(owner)) == owner else None
        except (ValueError, TypeError, AttributeError):
            return None

    def _active(self):
        return self.sync is not None and self.sync.account.snapshot().get('status') == 'active'

    def request(self, ranks, identity, pins, *, known_remote=()):
        """Ask for this identity's list; CommunitySync sends it at most once per lease.

        The list covers the whole ladder, so its key is the account, the
        identity and the calibration revision: never the PB or the rival
        settings. A target with no strategy never asks.
        """
        if not self._active() or identity is None or not identity.strategy:
            return ()
        key = (self.owner(), identity, getattr(ranks, 'calibration_revision', None))
        if key != self.key:
            self.key, self.windows = key, match_windows(ranks, identity)
        if not self.windows:
            return ()
        remote = self.remote_pins(pins, known_remote=known_remote)
        self.sync.request_pool(identity, frame_windows=self.windows, pinned_ids=remote)
        return remote

    def remote_pins(self, pins, *, known_remote=()):
        """Fresh remote provenance or owner-scoped saved provenance permits IO.

        Re-derived only when the choices or the population change, so a quiet
        cycle never looks a recording up in a database.
        """
        stamp = (self.owner(), tuple(sorted(pins.items())), tuple(known_remote),
                 getattr(self.population, 'generation', None))
        if stamp == self._pins_key:
            return self._pins
        known = set(known_remote)
        result = []
        for identifier in pins.values():
            if len(result) >= MAX_RIVALS:
                break
            if identifier in result:
                continue
            try:
                checked_hash(identifier)
            except (ValueError, TypeError):
                continue
            try:
                row = self.population.get(identifier)
            except (LookupError, ValueError):
                if identifier in known:
                    result.append(identifier)
                continue
            if row.get('provenance') == 'community-unverified':
                result.append(identifier)
        self._pins_key, self._pins = stamp, tuple(result)
        return self._pins

    def coverage(self, identity):
        if (not self._active() or identity is None or not identity.strategy):
            return None
        return self.population.coverage(identity)

    def complete(self, identity):
        coverage = self.coverage(identity)
        return coverage is None or coverage['complete']

    def list_state(self, identity):
        """Where this identity's rival list stands, for the lineup's state.

        'signed_out' (no cloud), 'pending' (asked, not answered), 'held' (an
        answer is cached, possibly empty), 'offline', 'unsupported' (the cloud
        has no such list), or 'absent' (never asked: the overlay is off).
        """
        if self.owner() is None or identity is None or not identity.strategy:
            return 'signed_out'
        status = self.sync.pool_status(identity)
        if status == 'pending':
            return 'pending'
        coverage = self.coverage(identity)
        if coverage is not None and coverage.get('generation') is not None:
            return 'held'
        return status or 'absent'

    def refused(self):
        """Recordings that can never download this session; automatic seats skip them."""
        return self.sync.refused_assets() if self.sync is not None else frozenset()

    def status(self, identity):
        if self.downloading:
            return 'Downloading rivals…'
        coverage = self.coverage(identity)
        if coverage is not None and not coverage['complete']:
            if self.sync.last_error:
                return 'Community rivals are unavailable. Your saved ghosts still work.'
            return 'Showing a partial rival list.' if coverage['generation'] else 'Finding rivals…'
        return None

    def stage(self, desired, *, fetch=True):
        """Every chosen seat, with `pending` on recordings still downloading.

        The lineup shows a pending seat with its player and colour at once;
        native playback receives only rows whose bytes are cached. Downloads
        start only when `fetch` (the overlay is on).
        """
        def ready(row):
            return (self.sync is None or row.get('provenance') != 'community-unverified'
                    or self.population.is_ready(row['id']))
        rows = tuple({**row, 'pending': not ready(row)} for row in desired)
        waiting = tuple(row['id'] for row in rows if row['pending'])
        self.downloading = bool(waiting)
        if waiting and fetch:
            self.sync.request_assets(waiting)
        return self._unique_players(rows)

    @staticmethod
    def _unique_players(rows):
        ids, players = set(), set()
        result = []
        for row in rows:
            if row['id'] not in ids and (row.get('ghost', True) is None or player_key(row) not in players):
                ids.add(row['id'])
                if row.get('ghost', True) is not None:
                    players.add(player_key(row))
                result.append(row)
        return tuple(sorted(result, key=lambda row: row['slot']))

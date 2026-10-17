"""Cached rank-band requests and download staging, outside native playback."""
from dataclasses import replace
from time import monotonic
from uuid import UUID

from sm64_events.ghost.adaptive import MAX_RIVALS, player_key
from sm64_events.ghost.community_catalog import match_windows
from sm64_events.ghost.portable import checked_hash
from sm64_events.ranks.region_scope import SHARED, VERSIONS


def pools(identity):
    """The community pools one practice reads, its running ROM's first. A
    shared region scope reads the other ROM's pool too (`ranks/region_scope.py`)."""
    if getattr(identity, "region_scope", None) != SHARED:
        return (identity,)
    return (identity, *(replace(identity, game_version=rom) for rom in VERSIONS
                        if rom != identity.game_version))


class CommunityRivals:
    def __init__(self, sync, population):
        self.sync, self.population = sync, population
        self.key, self.windows = None, ()
        self.next_request = 0
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

    def request(self, ranks, identity, pb, options, pins, *, known_remote=()):
        if self.sync is None:
            return ()
        key = (self.owner(), identity, getattr(ranks, 'calibration_revision', None),
               pb.get('step') if pb else None, tuple(sorted(options.items())), tuple(sorted(pins.items())))
        if key != self.key:
            self.key, self.windows = key, match_windows(ranks, identity, pb, options)
            self.next_request = 0
        if self.windows and monotonic() >= self.next_request:
            remote = self.remote_pins(pins, known_remote=known_remote)
            running, *others = pools(identity)
            self.sync.request_pool(running, frame_windows=self.windows, pinned_ids=remote)
            for other in others:
                self.sync.request_pool(other, frame_windows=self.windows)
            self.next_request = monotonic() + 5
            return remote
        return ()

    def remote_pins(self, pins, *, known_remote=()):
        """Fresh remote provenance or owner-scoped saved provenance permits IO."""
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
        return tuple(result)

    def coverage(self, identity):
        if self.sync is None or self.sync.account.snapshot().get('status') != 'active':
            return None
        running, *others = pools(identity)
        result = self.population.coverage(running)
        if others:
            result = {**result, 'complete': result['complete'] and all(
                self.population.coverage(other)['complete'] for other in others)}
        return result

    def complete(self, identity):
        coverage = self.coverage(identity)
        return coverage is None or coverage['complete']

    def status(self, identity):
        if self.downloading:
            return 'Downloading rivals…'
        coverage = self.coverage(identity) if identity is not None else None
        if coverage is not None and not coverage['complete']:
            if self.sync.last_error:
                return 'Community rivals are unavailable. Your saved ghosts still work.'
            return 'Showing a partial rival list.' if coverage['generation'] else 'Finding rivals…'
        return None

    def stage(self, desired, previous):
        """Keep compatible playable rivals until the desired bytes are cached."""
        if self.sync is None:
            return self._unique_players(desired)
        def ready(row):
            return row.get('provenance') != 'community-unverified' or self.population.is_ready(row['id'])
        self.downloading = any(not ready(row) for row in desired)
        if not self.downloading:
            return self._unique_players(desired)
        self.sync.request_assets(tuple(row['id'] for row in desired if row.get('provenance') == 'community-unverified'))
        wanted = {row['slot'] for row in desired}
        slots = {row['slot']: row for row in previous if row['slot'] in wanted and ready(row)}
        for row in desired:
            if ready(row):
                slots[row['slot']] = row
        # A ready replacement takes precedence over an old recording of that
        # player carried in a different slot while another asset downloads.
        fresh = {row['slot']: row['id'] for row in desired if ready(row)}
        ordered = sorted(slots.values(), key=lambda row: (fresh.get(row['slot']) != row['id'], row['slot']))
        return self._unique_players(ordered)

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

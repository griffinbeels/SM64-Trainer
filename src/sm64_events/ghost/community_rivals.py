"""Cached rank-band requests and download staging, outside native playback."""
from time import monotonic

from sm64_events.ghost.community_catalog import match_windows


class CommunityRivals:
    def __init__(self, sync, population):
        self.sync, self.population = sync, population
        self.key, self.windows = None, ()
        self.next_request = 0
        self.downloading = False

    def request(self, ranks, identity, pb, options, pins):
        if self.sync is None:
            return
        key = (identity, getattr(ranks, 'calibration_revision', None),
               pb.get('step') if pb else None, tuple(sorted(options.items())), tuple(sorted(pins.items())))
        if key != self.key:
            self.key, self.windows = key, match_windows(ranks, identity, pb, options)
            self.next_request = 0
        if self.windows and monotonic() >= self.next_request:
            self.sync.request_pool(identity, frame_windows=self.windows, pinned_ids=self.remote_pins(pins))
            self.next_request = monotonic() + 5

    def remote_pins(self, pins):
        """Private choices stay local; only community IDs need a server lease."""
        result = []
        for identifier in pins.values():
            try:
                row = self.population.get(identifier)
            except (LookupError, ValueError):
                continue
            if row.get('provenance') == 'community-unverified':
                result.append(identifier)
        return tuple(result)

    def coverage(self, identity):
        if self.sync is None or self.sync.account.snapshot().get('status') != 'active':
            return None
        return self.population.coverage(identity)

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
            return desired
        def ready(row):
            return row.get('provenance') != 'community-unverified' or self.population.is_ready(row['id'])
        self.downloading = any(not ready(row) for row in desired)
        if not self.downloading:
            return desired
        self.sync.request_assets(tuple(row['id'] for row in desired if row.get('provenance') == 'community-unverified'))
        wanted = {row['slot'] for row in desired}
        slots = {row['slot']: row for row in previous if row['slot'] in wanted and ready(row)}
        for row in desired:
            if ready(row):
                slots[row['slot']] = row
        ids = set()
        result = []
        for slot in sorted(slots):
            row = slots[slot]
            if row['id'] not in ids:
                ids.add(row['id'])
                result.append(row)
        return tuple(result)

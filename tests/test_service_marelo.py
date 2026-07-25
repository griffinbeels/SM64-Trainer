# tests/test_service_marelo.py
"""MARELO rank exclusions + celebration watermarks (spec
2026-07-24-marelo-rank-system). pytest-asyncio isn't a project dependency --
tests/test_tracker_service.py drives async service methods with plain
asyncio.run() inside sync test functions, and this file follows suit rather
than reaching for @pytest.mark.asyncio, which would silently no-op."""
import asyncio


def test_exclusion_round_trips_and_broadcasts(service):
    assert service.rank_excluded() == set()
    asyncio.run(service.set_rank_excluded("star:1:0", True))
    assert service.rank_excluded() == {"star:1:0"}
    asyncio.run(service.set_rank_excluded("star:1:0", False))
    assert service.rank_excluded() == set()


def test_excluding_twice_is_idempotent(service):
    asyncio.run(service.set_rank_excluded("star:1:0", True))
    asyncio.run(service.set_rank_excluded("star:1:0", True))
    assert service.rank_excluded() == {"star:1:0"}


def test_ack_raises_the_watermark(service):
    assert service.marelo_watermarks() == {}
    asyncio.run(service.ack_celebration("overall", 21))
    assert service.marelo_watermarks()["overall"] == 21


def test_ack_never_lowers_a_watermark(service):
    asyncio.run(service.ack_celebration("overall", 21))
    asyncio.run(service.ack_celebration("overall", 5))
    assert service.marelo_watermarks()["overall"] == 21


def test_sync_lowers_a_watermark_on_a_drop_so_reclimbing_celebrates(service):
    # sync_watermark never CREATES a watermark (that's seed_watermark's job,
    # asserted below) -- seed the baseline first, exactly as the real /marelo
    # payload builder does before it calls sync_watermark on every request.
    service.seed_watermark("overall", 21)
    service.sync_watermark("overall", 9)
    assert service.marelo_watermarks()["overall"] == 9


def test_sync_never_raises_a_watermark(service):
    service.seed_watermark("overall", 9)
    service.sync_watermark("overall", 30)
    assert service.marelo_watermarks()["overall"] == 9


def test_sync_on_an_unknown_scope_does_nothing(service):
    service.sync_watermark("route:7", 12)
    assert "route:7" not in service.marelo_watermarks()


def test_seed_writes_a_first_watermark_but_never_overwrites(service):
    """A scope's FIRST rank is not a rank-up: seeding it silently is what
    stops the whole backlog celebrating at once the first time it is viewed."""
    service.seed_watermark("route:7", 12)
    assert service.marelo_watermarks()["route:7"] == 12
    service.seed_watermark("route:7", 30)
    assert service.marelo_watermarks()["route:7"] == 12


# -- entity-level watermarks (task-f1: per-entity rank-up detection) ---------
#
# Stored under their OWN ui_state KV (`entity_rank_watermarks`), never merged
# into `marelo_watermarks`: scope ids ("overall", "route:3", "course:9") and
# entity keys ("star:6:0", "segment:3") don't collide by prefix today, but a
# second KV makes that true by CONSTRUCTION instead of by convention, and
# keeps each dict's key count bounded by what it actually is (a handful of
# scopes vs. up to ~102 entities) rather than one growing blob.

def test_entity_watermarks_starts_empty(service):
    assert service.entity_watermarks() == {}


def test_ack_entity_celebration_raises_the_watermark(service):
    assert service.entity_watermarks() == {}
    asyncio.run(service.ack_entity_celebration("star:6:0", 21))
    assert service.entity_watermarks()["star:6:0"] == 21


def test_ack_entity_celebration_never_lowers_a_watermark(service):
    asyncio.run(service.ack_entity_celebration("star:6:0", 21))
    asyncio.run(service.ack_entity_celebration("star:6:0", 5))
    assert service.entity_watermarks()["star:6:0"] == 21


def test_ack_entity_celebration_never_touches_scopes_or_other_entities(service):
    """Proves the no-collision constraint holds in practice, not just by
    inspection of the key shapes."""
    asyncio.run(service.ack_celebration("overall", 7))
    asyncio.run(service.ack_entity_celebration("star:6:0", 21))
    asyncio.run(service.ack_entity_celebration("star:7:1", 3))
    assert service.marelo_watermarks() == {"overall": 7}
    assert service.entity_watermarks() == {"star:6:0": 21, "star:7:1": 3}


def test_sync_and_seed_seeds_new_entities_silently(service):
    """First-ever score for two entities in one batch call: both get
    seeded, and the returned pre-seed watermark is None for each -- what
    celebration_delta needs to treat a first rank as not a rank-up."""
    result = service.sync_and_seed_entity_watermarks({"star:6:0": 4, "star:7:1": 10})
    assert result == {"star:6:0": None, "star:7:1": None}
    assert service.entity_watermarks() == {"star:6:0": 4, "star:7:1": 10}


def test_sync_and_seed_never_raises_an_existing_watermark(service):
    service.sync_and_seed_entity_watermarks({"star:6:0": 4})
    result = service.sync_and_seed_entity_watermarks({"star:6:0": 30})
    assert result == {"star:6:0": 4}
    assert service.entity_watermarks()["star:6:0"] == 4    # only ack raises


def test_sync_and_seed_lowers_on_a_drop_so_reclimbing_celebrates(service):
    service.sync_and_seed_entity_watermarks({"star:6:0": 21})
    result = service.sync_and_seed_entity_watermarks({"star:6:0": 9})
    assert result == {"star:6:0": 9}
    assert service.entity_watermarks()["star:6:0"] == 9


def test_sync_and_seed_leaves_unrelated_entities_untouched(service):
    service.sync_and_seed_entity_watermarks({"star:6:0": 21, "star:7:1": 5})
    service.sync_and_seed_entity_watermarks({"star:6:0": 2})
    assert service.entity_watermarks() == {"star:6:0": 2, "star:7:1": 5}


def test_sync_and_seed_makes_exactly_one_db_write_for_a_whole_corpus_batch(
        service, monkeypatch):
    """The perf-sensitive contract driving the storage choice: /api/marelo
    scores the full ~102-entity rankable corpus on every request, so a
    per-entity get_state/set_state round trip (the scope path's shape)
    would turn one request into up to ~200 sqlite commits. This must
    collapse to one read and, at most, one write regardless of corpus
    size."""
    writes = []
    original_set_state = service.db.set_state
    def spy_set_state(key, value):
        writes.append(key)
        return original_set_state(key, value)
    monkeypatch.setattr(service.db, "set_state", spy_set_state)

    reads = []
    original_get_state = service.db.get_state
    def spy_get_state(key, default):
        reads.append(key)
        return original_get_state(key, default)
    monkeypatch.setattr(service.db, "get_state", spy_get_state)

    corpus = {f"star:{index}:0": index for index in range(50)}
    service.sync_and_seed_entity_watermarks(corpus)
    assert reads == ["entity_rank_watermarks"]
    assert writes == ["entity_rank_watermarks"]


def test_sync_and_seed_with_no_changes_makes_no_write(service, monkeypatch):
    """A steady-state /api/marelo poll (nothing moved since the last
    request) should not commit at all."""
    service.sync_and_seed_entity_watermarks({"star:6:0": 21})
    writes = []
    original_set_state = service.db.set_state
    def spy(key, value):
        writes.append(key)
        return original_set_state(key, value)
    monkeypatch.setattr(service.db, "set_state", spy)
    service.sync_and_seed_entity_watermarks({"star:6:0": 21})   # same tier again
    assert writes == []

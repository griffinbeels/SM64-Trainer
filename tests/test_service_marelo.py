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

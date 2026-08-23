# tests/test_service_marelo.py
"""MARELO rank exclusions + celebration watermarks (spec
2026-07-24-marelo-rank-system). pytest-asyncio isn't a project dependency --
tests/test_tracker_service.py drives async service methods with plain
asyncio.run() inside sync test functions, and this file follows suit rather
than reaching for @pytest.mark.asyncio, which would silently no-op."""
import asyncio

from sm64_events.ranks import scopes


def test_exclusion_round_trips_and_broadcasts(service):
    baseline = service.rank_excluded()          # the DEFAULT set, see below
    assert "star:1:0" not in baseline
    asyncio.run(service.set_rank_excluded("star:1:0", True))
    assert service.rank_excluded() == baseline | {"star:1:0"}
    asyncio.run(service.set_rank_excluded("star:1:0", False))
    assert service.rank_excluded() == baseline


def _segments_by_category(service):
    """One segment per seeded category, inserted the way the defaults corpus
    lands them (`category` on the definition) -- the fresh test db seeds only
    the ten category-less legacy segments, so the rule's two exemptions have
    to be planted to be judged. Idempotent across the module's tests."""
    have = {definition["name"] for definition in service.db.segment_defs()}
    # Distinct names: the fresh db's ten LEGACY seeds already carry "Bowser
    # 1" and "LBLJ" with NO category (the corpus seed stamps categories on
    # them only when main.py applies it), so those names would collide.
    for name, category in (("Planted Bowser Fight", "Bowser Fights"),
                           ("Planted 100 Coin Exit", "100 Coin Exit"),
                           ("Planted Movement", "Castle Movement"),
                           ("Planted Trick", "Tricks")):
        if name not in have:
            service.db.insert_segment_def(
                name, [{"type": "level_enter", "to": 6}], [{"type": "star_grabbed"}],
                [], "2026-08-23T00:00:00Z", category=category)
    out = {}
    for definition in service.db.segment_defs():
        out.setdefault(definition["category"], []).append(f"segment:{definition['id']}")
    return out


def test_segments_outside_bowser_fights_and_hundred_coin_exits_are_excluded_by_default(service):
    """Round 1 of the ranked leaderboard, fifth read (2026-08-23): "by
    default all segments should be ignored by default (other than the
    Bowser stages / 100C stars, which I think are technically segments)...
    apply this to the overall ranking + all routes by default. The user can
    go in and manually include those later". Keyed on the seeded category,
    never a name list, so a future Bowser fight inherits it."""
    by_category = _segments_by_category(service)
    excluded = service.rank_excluded()
    assert by_category["Bowser Fights"] and by_category["100 Coin Exit"]
    assert by_category["Castle Movement"] and by_category["Tricks"]
    for key in by_category["Bowser Fights"] + by_category["100 Coin Exit"]:
        assert key not in excluded, key
    # Movements, tricks, AND a segment with no category at all (his own,
    # hand-built) -- "all segments" means all. The category-less legacy
    # seeds include the three Bowser course entries, exempt by seed key
    # (the next test); every OTHER one is ignored.
    pipe_entries = {f"segment:{d['id']}" for d in service.db.segment_defs()
                    if d.get("seed_key") in scopes.RANKED_SEGMENT_SEED_KEYS}
    uncategorised = [key for key in by_category[None] if key not in pipe_entries]
    assert uncategorised, "no category-less segment left to judge"
    for key in by_category["Castle Movement"] + by_category["Tricks"] + uncategorised:
        assert key in excluded, key
    assert not any(key.startswith("star:") for key in excluded)


def test_the_three_bowser_course_entries_rank_by_default(service):
    """Sixth read (2026-08-23): "Looks like you accidentally ignored BitFS
    Pipe Entry and BitS Pipe Entry -- these should not be ignored in any
    route, because those are just the Bowser Course entries (i.e., No
    Reds)." They are `Castle Movement` in the seed like the reds-inclusive
    pipe runs beside them, so the exemption is by seed key. The fresh db's
    legacy seeds carry exactly those three keys with no category at all."""
    by_key = {definition["seed_key"]: f"segment:{definition['id']}"
              for definition in service.db.segment_defs() if definition.get("seed_key")}
    excluded = service.rank_excluded()
    for seed_key in ("seg:bitdw-pipe", "seg:bitfs-pipe", "seg:bits-pipe"):
        assert by_key[seed_key] not in excluded, seed_key
    # ...and the castle-to-BitS movement beside them, plus every trick, stays
    # ignored: the exemption is the pipe ENTRY, not everything near a pipe.
    for seed_key in ("seg:bits-entry", "seg:lblj", "seg:mips-clip", "seg:lakitu-skip"):
        assert by_key[seed_key] in excluded, seed_key


def test_including_a_default_excluded_segment_sticks_and_excluding_it_again_clears(service):
    trick = _segments_by_category(service)["Tricks"][0]
    assert trick in service.rank_excluded()
    asyncio.run(service.set_rank_excluded(trick, False))
    assert trick not in service.rank_excluded()
    assert trick in service.db.get_state("rank_included", [])
    assert trick not in service.db.get_state("rank_excluded", [])
    asyncio.run(service.set_rank_excluded(trick, True))
    assert trick in service.rank_excluded()
    # Back to the default: the override is CLEARED, not recorded twice.
    assert trick not in service.db.get_state("rank_included", [])
    assert trick not in service.db.get_state("rank_excluded", [])


def test_excluding_twice_is_idempotent(service):
    baseline = service.rank_excluded()
    asyncio.run(service.set_rank_excluded("star:1:0", True))
    asyncio.run(service.set_rank_excluded("star:1:0", True))
    assert service.rank_excluded() == baseline | {"star:1:0"}


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


# -- entity-level watermarks: DELETED (task 0012, 2026-07-26) ----------------
#
# `entity_watermarks`, `sync_and_seed_entity_watermarks` and
# `ack_entity_celebration` are gone with the per-entity celebrations they
# existed for: a star's or segment's own rank-up is performed live by the rank
# banner climbing (ui/rankclimb.js), so nothing is held for later and nothing
# is acked. The guard that the methods are actually GONE rather than merely
# unused lives beside the endpoint that used to expose them,
# tests/test_ranks_api_marelo.py::test_the_service_holds_no_entity_watermarks.
# The SCOPE watermark tests above are untouched -- the full-screen MARELO
# overlay still needs holding and acking.

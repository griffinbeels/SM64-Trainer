"""The 100-coin exit-star classifier: what a finished run is filed under.

Spec 2026-08-03-hundred-coin-exit-variants. The rule under test is the user's,
verbatim: the exit star is observed and the sub-strategy is chosen, so a run
that ends on a different variant MOVES variant and KEEPS its sub-strategy.
"""
import pytest

from sm64_events.ranks.standards import RankStandards, VARIANT_SEP, qualify
from sm64_events.tracking.hundred_coin import classify
from sm64_events.tracking.projection import Projector

# CCM as the seed really ships it: two exit stars, and BOTH define "Standard"
# and "Open" — which is why a bare strategy name cannot identify a ladder here.
CCM_VARIANTS = {"100c + Slide": 0, "100c + Race": 2}
CCM_STRATS = ["100c + Slide · Standard", "100c + Slide · Open",
              "100c + Race · Standard", "100c + Race · Open",
              "100c + Race · Atmpas Route"]


def test_a_run_ending_on_the_selected_variant_is_left_alone():
    assert classify(CCM_STRATS, CCM_VARIANTS, "100c + Race · Open", 2) \
        == "100c + Race · Open"


def test_a_run_ending_elsewhere_moves_variant_and_keeps_the_sub_strategy():
    assert classify(CCM_STRATS, CCM_VARIANTS, "100c + Slide · Open", 2) \
        == "100c + Race · Open"


def test_a_sub_strategy_the_new_variant_lacks_falls_back_to_its_first():
    assert classify(CCM_STRATS, CCM_VARIANTS, "100c + Race · Atmpas Route", 0) \
        == "100c + Slide · Standard"


def test_nothing_selected_yet_takes_the_variants_first_strategy():
    assert classify(CCM_STRATS, CCM_VARIANTS, None, 2) == "100c + Race · Standard"


def test_a_failed_run_has_no_exit_star_and_so_no_answer():
    """THE reason the user's 868 historical 100-coin attempts still prune: they
    are all deaths and resets, so nothing here labels them and they stay
    unlabelled. A None answer means "leave what is remembered alone"."""
    assert classify(CCM_STRATS, CCM_VARIANTS, "100c + Slide · Open", None) is None


def test_an_exit_star_the_community_has_no_times_for_gets_no_answer():
    assert classify(CCM_STRATS, CCM_VARIANTS, "100c + Slide · Open", 5) is None


def test_an_ordinary_star_with_no_variants_is_never_touched():
    assert classify(["Standard", "Open"], {}, "Open", 3) is None


def test_the_longest_matching_label_wins():
    """A label that is a prefix of another must not steal its strategies."""
    variants = {"100c + Reds": 3, "100c + Reds Alt": 4}
    strats = ["100c + Reds · Standard", "100c + Reds Alt · Standard"]
    assert classify(strats, variants, None, 4) == "100c + Reds Alt · Standard"
    assert classify(strats, variants, None, 3) == "100c + Reds · Standard"


# ---- the store's own half of the same rule ----

def _store(tmp_path, entity, body):
    path = tmp_path / "standards.json"
    path.write_text(f'{{"version": 1, "entities": {{"{entity}": {body}}}}}')
    store = RankStandards(path)
    store.load()
    return store


def test_variant_of_and_classify_agree_on_every_seeded_strategy(tmp_path):
    """The store resolves a strategy's variant for the UI; the classifier
    resolves it for the projector. Two readers of one rule, so they are pinned
    against each other rather than each tested alone."""
    store = _store(tmp_path, "star:4:6", """{
        "clock": "igt",
        "exit_variants": {"100c + Slide": 0, "100c + Race": 2},
        "strategies": {"100c + Slide · Standard": {}, "100c + Race · Open": {}}}""")
    for strategy in store.strategies("star:4:6"):
        label, star = store.variant_of("star:4:6", strategy)
        assert classify([strategy], store.exit_variants("star:4:6"), None, star) \
            == strategy
        assert strategy.startswith(label + VARIANT_SEP)


def test_strategy_groups_carry_the_leaf_the_dropdown_shows(tmp_path):
    store = _store(tmp_path, "star:4:6", """{
        "clock": "igt",
        "exit_variants": {"100c + Slide": 0, "100c + Race": 2},
        "strategies": {"100c + Slide · Standard": {"Gold": 1.0},
                       "100c + Race · Open": {"Gold": 2.0},
                       "Hand Edited": {"Gold": 3.0}}}""")
    groups = store.strategy_groups("star:4:6")
    assert [(g["label"], g["exit_star"]) for g in groups] == [
        ("100c + Slide", 0), ("100c + Race", 2), ("Other", None)]
    assert groups[0]["strategies"] == [
        {"name": "100c + Slide · Standard", "leaf": "Standard"}]
    # A name matching no variant is listed rather than dropped.
    assert groups[2]["strategies"] == [{"name": "Hand Edited", "leaf": "Hand Edited"}]


def test_an_ordinary_entity_has_no_groups_at_all(tmp_path):
    store = _store(tmp_path, "star:4:1",
                   '{"clock": "igt", "strategies": {"Standard": {"Gold": 1.0}}}')
    assert store.strategy_groups("star:4:1") == []
    assert store.variant_of("star:4:1", "Standard") is None


def test_creating_a_strategy_for_an_unlisted_exit_star_mints_a_variant(tmp_path):
    """The user's "define your own variant" path: the exit star names itself
    from our own star registry, and the caller is TOLD the stored name."""
    store = _store(tmp_path, "star:4:6",
                   '{"clock": "igt", "exit_variants": {"100c + Slide": 0},'
                   ' "strategies": {}}')
    stored = store.create_strategy("star:4:6", "My Route", exit_star=5)
    assert stored == qualify("100c + Wall Kicks Will Work", "My Route")
    assert store.exit_variants("star:4:6")["100c + Wall Kicks Will Work"] == 5
    assert stored in store.strategies("star:4:6")


def test_creating_one_for_a_known_exit_star_reuses_that_variants_label(tmp_path):
    store = _store(tmp_path, "star:4:6",
                   '{"clock": "igt", "exit_variants": {"100c + Slide": 0},'
                   ' "strategies": {}}')
    assert store.create_strategy("star:4:6", "My Route", exit_star=0) \
        == "100c + Slide · My Route"
    assert list(store.exit_variants("star:4:6")) == ["100c + Slide"]


def test_creating_one_with_no_exit_star_stores_the_plain_name(tmp_path):
    store = _store(tmp_path, "star:4:1", '{"clock": "igt", "strategies": {}}')
    assert store.create_strategy("star:4:1", "Standard") == "Standard"


def test_a_user_minted_variant_survives_a_seed_bump(tmp_path):
    """A variant xcams has no times for is user-created data, exactly like the
    strategies filed under it. Dropping it at reconcile would leave those
    strategies listed under "Other", which reads as data loss."""
    seed = tmp_path / "seed.json"
    seed.write_text('{"version": 9, "entities": {"star:4:6": {"clock": "igt",'
                    ' "exit_variants": {"100c + Slide": 0},'
                    ' "strategies": {"100c + Slide · Standard": {"Gold": 1.0}}}}}')
    stored = tmp_path / "standards.json"
    stored.write_text('{"version": 1, "entities": {"star:4:6": {"clock": "igt",'
                      ' "exit_variants": {"100c + Slide": 0, "100c + Mine": 5},'
                      ' "strategies": {"100c + Mine · Route": {"Gold": 2.0}}}}}')
    store = RankStandards(stored, seed_path=seed)
    store.load()
    assert store.exit_variants("star:4:6") == {"100c + Slide": 0, "100c + Mine": 5}
    assert "100c + Mine · Route" in store.strategies("star:4:6")
    assert "100c + Slide · Standard" in store.strategies("star:4:6")


# ---- end to end, through the real 100-coin engine ----

def _wf_engine_service(tmp_path, engine_enabled=True):
    """A service whose course-2 (WF) 100-coin engine is the real seeded shape,
    with two exit-star variants in its standards. Same def as
    tests/test_tracker_service.py's own 100-coin fixture."""
    import asyncio

    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService

    db = Database(tmp_path / "t.db")
    engine_id = db.insert_segment_def(
        "course 2 100 Coins -> Exit",
        start_triggers=[{"type": "level_enter", "to": 24},
                        {"type": "attempt_anchor", "level": 24}],
        end_triggers=[{"type": "star_grabbed", "course": 2, "star": s}
                      for s in range(6)],
        guards=[], waypoints=[[{"type": "star_grabbed", "course": 2, "star": 6}]],
        created_utc="2026-07-28T00:00:00Z")
    if not engine_enabled:
        # By id, never a literal: the schema migration already seeds ten
        # legacy tricks, so this def is 11, not 1 — and disabling the wrong
        # row leaves the engine armed while the test claims it is off.
        db.update_segment_def(engine_id, enabled=0)
    path = tmp_path / "standards.json"
    path.write_text("""{"version": 1, "entities": {"star:2:6": {
        "clock": "igt",
        "exit_variants": {"100c + Reds": 3, "100c + Blue": 0},
        "strategies": {"100c + Reds · Standard": {"Gold": 100.0},
                       "100c + Reds · Open": {"Gold": 99.0},
                       "100c + Blue · Open": {"Gold": 98.0}}}}}""")
    ranks = RankStandards(path)
    ranks.load()
    service = TrackerService(db, Broadcaster(), ranks=ranks)
    asyncio.run(service.start())
    return db, service


def _hundred_coin_run(service, base, exit_star):
    """Enter WF, grab the 100 coins, leave on `exit_star`."""
    import asyncio

    from tests.test_tracker_service import ev, star
    asyncio.run(service.publish(ev("level_changed", base, {"from": 16, "to": 24})))
    asyncio.run(service.publish(star(base + 100, course=2, star_id=6,
                                     igt=base + 100)))
    asyncio.run(service.publish(star(base + 200, course=2, star_id=exit_star,
                                     igt=base + 200)))


def test_the_exit_star_you_finish_on_classifies_the_run(tmp_path):
    db, service = _wf_engine_service(tmp_path)
    _hundred_coin_run(service, 900, exit_star=3)
    rows = [a for a in db.attempts() if a.star_id == 6 and a.outcome == "success"]
    assert [a.strat_tag for a in rows] == ["100c + Reds · Standard"]
    # ...and the card follows the run, so the next attempt starts there.
    assert service.strat_by_star[(2, 6)] == "100c + Reds · Standard"


def test_finishing_on_another_variant_keeps_the_sub_strategy(tmp_path):
    """The user's rule end to end: he is on Reds/Open, exits on the Blue star,
    and the row is filed under Blue/Open — not Blue's first strategy, and not
    the Reds ladder he did not run."""
    import asyncio

    db, service = _wf_engine_service(tmp_path)
    asyncio.run(service.set_target(2, 6, strat_tag="100c + Reds · Open"))
    _hundred_coin_run(service, 900, exit_star=0)
    rows = [a for a in db.attempts() if a.star_id == 6 and a.outcome == "success"]
    assert [a.strat_tag for a in rows] == ["100c + Blue · Open"]


def test_a_failed_100_coin_run_stays_unlabelled(tmp_path):
    """No exit star, so nothing classifies it — which is exactly why the 868
    historical 100-coin attempts (all deaths and resets, no strategy) remain
    prunable rather than being resurrected by this feature (user's call,
    2026-08-03: "just drop 'em")."""
    import asyncio

    from tests.test_tracker_service import ev, star

    db, service = _wf_engine_service(tmp_path)
    asyncio.run(service.publish(ev("level_changed", 900, {"from": 16, "to": 24})))
    asyncio.run(service.publish(star(1000, course=2, star_id=6, igt=1000)))
    asyncio.run(service.publish(ev("death", 1200, {"igt_frames": 1200})))
    rows = [a for a in db.attempts() if a.star_id == 6]
    assert rows and all(a.strat_tag is None for a in rows)

    from sm64_events.tracking.prune import unlabelled
    assert all(unlabelled(a) for a in rows)


def test_a_hand_reclassification_still_outranks_the_derived_answer(tmp_path):
    """A manual answer beats a derived one — `_strat_overrides` is applied on
    top, the same precedence an ordinary star attempt already has."""
    import asyncio

    db, service = _wf_engine_service(tmp_path)
    _hundred_coin_run(service, 900, exit_star=3)
    row = next(a for a in db.attempts()
               if a.star_id == 6 and a.outcome == "success")
    asyncio.run(service.set_attempt_strat(row.id, "100c + Blue · Open"))
    again = next(a for a in db.attempts() if a.id == row.id)
    assert again.strat_tag == "100c + Blue · Open"


def test_an_ordinary_star_is_untouched_by_any_of_this(tmp_path):
    """Stars 0-5 never enter this path — the engine only closes on the
    100-coin star's own exit, and an ordinary grab keeps its own strategy."""
    import asyncio

    from tests.test_tracker_service import star

    db, service = _wf_engine_service(tmp_path)
    asyncio.run(service.set_target(2, 1, strat_tag="Cannonless"))
    asyncio.run(service.publish(star(1000, course=2, star_id=1, igt=1000)))
    row = next(a for a in db.attempts() if a.star_id == 1)
    assert row.strat_tag == "Cannonless"


def test_a_variant_label_is_never_itself_a_strategy_name(tmp_path):
    """A label alone must not select anything — it names a GROUP. The dropdown
    renders a heading for it, and a heading that is also a pickable value is
    how a group gets set as the active strategy."""
    store = _store(tmp_path, "star:4:6", """{
        "clock": "igt", "exit_variants": {"100c + Slide": 0},
        "strategies": {"100c + Slide · Standard": {"Gold": 1.0}}}""")
    assert "100c + Slide" not in store.strategies("star:4:6")
    assert classify(store.strategies("star:4:6"),
                    store.exit_variants("star:4:6"), None, 0) \
        == "100c + Slide · Standard"


# ---- one reset, one row ----

def _reset(service, frame, igt):
    """A reset the projector counts: an anchor plus the `mario_acted` EVENT.

    Without the event `_unacted_open` discards the span as reset-spam, and a
    test asserting "no duplicates" would pass on ZERO rows — which is why
    every assertion below counts rows rather than checking for their absence.
    """
    import asyncio

    from tests.test_tracker_service import ev
    asyncio.run(service.publish(ev("practice_reset", frame, {
        "igt_frames_before": igt, "mario_acted": True,
        "acted_tracking": True, "paused_frames_before": 0})))
    asyncio.run(service.publish(ev("mario_acted", frame + 10, {})))


def test_a_reset_on_a_targeted_100_coin_star_records_ONE_row(tmp_path):
    """Live report 2026-08-03: "Resetting during a 100 coins star triggers two
    resets, for some reason." It did — his own journal carried, for each of
    three reset spans, a star-namespace row AND a segment-namespace row with
    the same journal id, the same span and the same strategy.

    The engine turns the reset into its own row (which feed() reattributes to
    this very star), and the plain attempt for the active target recorded the
    same span again. The GRAB path had always suppressed its half; the reset
    path never did. Invisible until 100-coin stars got rank standards, because
    before that nothing could set a strategy on one, so BOTH rows were
    unlabelled and the startup prune ate them.
    """
    import asyncio

    from sm64_events.tracking.projection import journal_id
    from tests.test_tracker_service import ev

    db, service = _wf_engine_service(tmp_path)
    asyncio.run(service.publish(ev("level_changed", 900, {"from": 16, "to": 24})))
    asyncio.run(service.set_target(2, 6, strat_tag="100c + Reds · Standard"))
    for frame, igt in ((1000, 0), (1400, 400), (1800, 400)):
        _reset(service, frame, igt)

    rows = [a for a in db.attempts() if a.star_id == 6 and a.outcome == "reset"]
    assert rows, "no reset recorded at all — the fixture never reached a run"
    ids = [journal_id(a.id) for a in rows]
    assert len(ids) == len(set(ids)), (
        f"one reset span recorded twice: {[(a.id, a.rta_frames) for a in rows]}")


def test_the_plain_attempt_still_records_when_no_engine_is_armed(tmp_path):
    """The fallback that keeps a retry visible: with the engine disabled,
    nothing is timing the course visit, so the ordinary star attempt is the
    only record there is — suppressing it would DELETE the row rather than
    de-duplicate it."""
    import asyncio

    from tests.test_tracker_service import ev

    db, service = _wf_engine_service(tmp_path, engine_enabled=False)
    asyncio.run(service.publish(ev("level_changed", 900, {"from": 16, "to": 24})))
    asyncio.run(service.set_target(2, 6, strat_tag="100c + Reds · Standard"))
    for frame, igt in ((1000, 0), (1400, 400)):
        _reset(service, frame, igt)
    from sm64_events.tracking.segments import SEGMENT_ATTEMPT_OFFSET
    rows = [a for a in db.attempts() if a.star_id == 6]
    assert rows and all(a.outcome == "reset" for a in rows), rows
    # Every one is a PLAIN star attempt: with the engine off there is no
    # segment-namespace row for these spans to have come from.
    assert all(a.id < SEGMENT_ATTEMPT_OFFSET for a in rows), [a.id for a in rows]


def test_leaving_the_course_still_records_the_abandoned_run(tmp_path):
    """A foreign level change cancels a strict waypoint def SILENTLY — no
    engine row — so the plain `abandoned` attempt is the only evidence the run
    happened. Suppressing on armed-state alone would have eaten it, which is
    why `_ENGINE_MIRRORED_OUTCOMES` omits `abandoned`."""
    import asyncio

    from tests.test_tracker_service import ev

    db, service = _wf_engine_service(tmp_path)
    asyncio.run(service.publish(ev("level_changed", 900, {"from": 16, "to": 24})))
    asyncio.run(service.set_target(2, 6, strat_tag="100c + Reds · Standard"))
    _reset(service, 1000, 0)
    asyncio.run(service.publish(ev("level_changed", 1500, {"from": 24, "to": 16})))
    assert any(a.star_id == 6 and a.outcome == "abandoned"
               for a in db.attempts()), [
        (a.star_id, a.outcome) for a in db.attempts()]


def test_a_death_on_a_targeted_100_coin_star_records_ONE_row(tmp_path):
    """Live report 2026-08-03, the same defect the reset fix left behind:
    "triggering a death caused TWO deaths simultaneously... when we have a 100
    coin star selected, there's always 2 deaths (I tested this across courses,
    with and without 100 coins selected)."

    ONE `death` event in the journal, two rows out of it. The reset fix put
    `_engine_records_this_too` in `_close`, and `_ENGINE_MIRRORED_OUTCOMES`
    already listed "death" — but `_close_by_death` calls `_build` DIRECTLY and
    so never consults the guard, which made that set member vacuous.
    """
    import asyncio

    from sm64_events.tracking.projection import journal_id
    from tests.test_tracker_service import ev

    db, service = _wf_engine_service(tmp_path)
    asyncio.run(service.publish(ev("level_changed", 900, {"from": 16, "to": 24})))
    asyncio.run(service.set_target(2, 6, strat_tag="100c + Reds · Standard"))
    _reset(service, 1000, 0)                      # arms the engine and acts
    asyncio.run(service.publish(ev("death", 1400, {"igt_frames": 400,
                                                   "cause": "fall"})))

    rows = [a for a in db.attempts() if a.star_id == 6 and a.outcome == "death"]
    assert rows, "no death recorded at all — the fixture never reached a run"
    ids = [journal_id(a.id) for a in rows]
    assert len(ids) == len(set(ids)), (
        f"one death recorded twice: {[(a.id, a.rta_frames) for a in rows]}")


def test_the_plain_death_still_records_when_no_engine_is_armed(tmp_path):
    """Same fallback the reset half keeps: with nothing timing the course
    visit, the ordinary star attempt is the only record of the death."""
    import asyncio

    from tests.test_tracker_service import ev

    db, service = _wf_engine_service(tmp_path, engine_enabled=False)
    asyncio.run(service.publish(ev("level_changed", 900, {"from": 16, "to": 24})))
    asyncio.run(service.set_target(2, 6, strat_tag="100c + Reds · Standard"))
    _reset(service, 1000, 0)
    asyncio.run(service.publish(ev("death", 1400, {"igt_frames": 400,
                                                   "cause": "fall"})))

    rows = [a for a in db.attempts() if a.star_id == 6 and a.outcome == "death"]
    assert len(rows) == 1, f"the only record of the death vanished: {rows}"


@pytest.mark.parametrize("outcome", sorted(Projector._ENGINE_MIRRORED_OUTCOMES))
def test_every_mirrored_outcome_is_actually_suppressed(tmp_path, outcome):
    """The MECHANISM against this bug recurring, rather than a third fix.

    `_ENGINE_MIRRORED_OUTCOMES` listed "death" while nothing asked about it —
    a *vacuous guard*, because `_close_by_death` builds its row directly
    instead of routing through `_close`. Any future member added to that set
    with no closer consulting the guard fails here instead of shipping as a
    duplicate row nobody notices for a month.
    """
    import asyncio

    from sm64_events.tracking.projection import journal_id
    from tests.test_tracker_service import ev

    db, service = _wf_engine_service(tmp_path)
    asyncio.run(service.publish(ev("level_changed", 900, {"from": 16, "to": 24})))
    asyncio.run(service.set_target(2, 6, strat_tag="100c + Reds · Standard"))
    _reset(service, 1000, 0)                   # arms the engine and acts
    closer = {
        "reset": lambda: _reset(service, 1400, 400),
        "death": lambda: asyncio.run(service.publish(
            ev("death", 1400, {"igt_frames": 400, "cause": "fall"}))),
        "hard_reset": lambda: asyncio.run(service.publish(
            ev("game_reset", 1400, {}))),
    }[outcome]
    closer()

    rows = [a for a in db.attempts()
            if a.star_id == 6 and a.outcome == outcome]
    assert rows, f"no {outcome} recorded at all — the fixture never ran"
    ids = [journal_id(a.id) for a in rows]
    assert len(ids) == len(set(ids)), (
        f"one {outcome} span recorded twice: "
        f"{[(a.id, a.rta_frames) for a in rows]}")

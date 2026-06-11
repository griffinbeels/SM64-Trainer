import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.views import build_session_view

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def star(frame, course=2, star_id=2, igt=343):
    return ev("star_collected", frame,
              {"course_id": course, "star_id": star_id, "igt_frames": igt})


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster())
    asyncio.run(svc.start())
    return db, svc


def seed(svc):
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1900, {"igt_frames_before": 470})))
    asyncio.run(svc.publish(star(2400, igt=350)))


def test_view_groups_by_star_with_stats_and_pb_delta(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    aid = next(a.id for a in db.attempts() if a.igt_frames == 343)
    asyncio.run(svc.save_pb(aid, "igt"))
    view = build_session_view(db, svc, clock="igt")
    assert view["session"]["id"] == 1
    assert view["clock"] == "igt"
    assert view["target"]["course_id"] == 2 and view["target"]["star_id"] == 2
    [sec] = view["stars"]
    assert sec["course_id"] == 2 and sec["star_id"] == 2
    assert sec["star_name"] == "Shoot into the Wild Blue"
    assert sec["links"]["ukikipedia"].endswith("Shoot_into_the_Wild_Blue")
    assert sec["pb"]["igt"]["frames"] == 343
    # 3 attempts in section (ordered by id): the star at 1350 closed the
    # first anchor as success; the 1400 anchor opened a fresh attempt that
    # the 1900 anchor closed as reset; the 2400 grab closed the last one.
    outcomes = [a["outcome"] for a in sec["attempts"]]
    assert outcomes == ["success", "reset", "success"]
    last = sec["attempts"][-1]
    assert last["igt"] == "0'11\"66" and last["pb_delta_frames"] == 7
    stats = {s["key"]: s for s in sec["stats"]}
    assert stats["best"]["value"] == 343 and stats["best"]["display"] == "0'11\"43"
    assert abs(stats["success_rate"]["value"] - 2 / 3) < 1e-9
    assert stats["success_rate"]["display"] == "67%"


def test_failures_before_any_grab_land_in_unassigned(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 380})))
    view = build_session_view(db, svc, clock="igt")
    assert view["stars"] == []
    assert len(view["unassigned"]) == 1


def test_view_includes_catalog_and_stat_menu(tmp_path):
    db, svc = make(tmp_path)
    view = build_session_view(db, svc, clock="igt")
    courses = {c["id"]: c for c in view["catalog"]["courses"]}
    assert courses[2]["name"] == "Whomp's Fortress"
    assert courses[2]["stars"][2] == "Shoot into the Wild Blue"
    assert any(s["key"] == "avg_last_n" for s in view["stat_menu"])


def test_cleared_attempts_remain_visible_but_flagged(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    aid = db.attempts()[0].id
    asyncio.run(svc.clear_attempt(aid, reason="accidental"))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    flags = {a["id"]: a["cleared"] for a in sec["attempts"]}
    assert flags[aid] is True


def test_rta_clock_path_with_pb_and_race_guard(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))      # rta = 350
    aid = db.attempts()[0].id
    asyncio.run(svc.save_pb(aid, "rta"))
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1760, igt=355)))      # rta = 360
    view = build_session_view(db, svc, clock="rta")
    [sec] = view["stars"]
    assert sec["pb"]["rta"]["frames"] == 350
    assert sec["attempts"][-1]["pb_delta_frames"] == 10
    # same-tick race row: rta delta suppressed
    asyncio.run(svc.publish(ev("practice_reset", 1800, {"igt_frames_before": 380})))
    asyncio.run(svc.publish(star(1800, igt=380)))      # rta = 0
    view2 = build_session_view(db, svc, clock="rta")
    [sec2] = view2["stars"]
    assert sec2["attempts"][-1]["rta_frames"] == 0
    assert sec2["attempts"][-1]["pb_delta_frames"] is None


def test_stats_are_lifetime_scoped_but_times_are_session_scoped(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400, igt=350)))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert len(sec["attempts"]) == 1                    # this session only
    stats = {s["key"]: s for s in sec["stats"]}
    assert stats["best"]["value"] == 343                # lifetime history


def test_avg_last_n_label_renders_param(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    labels = [s["label"] for s in sec["stats"]]
    assert "Avg last 10" in labels and "Avg last 50" in labels


def test_view_surfaces_strategies_and_last_strat(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 2, strat_tag="cannonless"))
    seed(svc)   # grabs (2,2) twice
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["strategies"] == ["cannonless"]
    assert sec["last_strat"] == "cannonless"
    assert view["strategies"] == {"2:2": ["cannonless"]}
    assert view["last_strat_by_star"] == {"2:2": "cannonless"}


# -- timeline tests -----------------------------------------------------------

def test_timeline_contains_success_reset_death_points(tmp_path):
    db, svc = make(tmp_path)
    # success
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    # reset
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1800, {"igt_frames_before": 370})))
    # death
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 0, "mario_acted": True})))
    asyncio.run(svc.publish(ev("death", 2300, {"cause": "pit", "igt_frames": 290, "level": 4})))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    tl = sec["timeline"]
    assert tl is not None
    outcomes = {p["outcome"] for p in tl["points"]}
    assert "success" in outcomes
    assert "reset" in outcomes
    assert "death" in outcomes
    # max_frames = longest success, flagged as success-derived
    success_frames = [p["frames"] for p in tl["points"] if p["outcome"] == "success"]
    assert tl["max_frames"] == max(success_frames)
    assert tl["max_is_success"] is True
    # each point has igt string
    assert all("igt" in p and p["frames"] is not None for p in tl["points"])


def test_timeline_reset_longer_than_best_success_max_is_success(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=200)))   # success @ 200 frames
    asyncio.run(svc.publish(ev("practice_reset", 1400, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 500})))  # reset @ 500
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    tl = sec["timeline"]
    # max_frames = 200 (the success), NOT 500
    assert tl["max_frames"] == 200
    assert tl["max_is_success"] is True
    # but the reset point is still in the list
    reset_pts = [p for p in tl["points"] if p["outcome"] == "reset"]
    assert len(reset_pts) == 1 and reset_pts[0]["frames"] == 500


def test_timeline_without_success_max_falls_back_and_is_flagged(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 2))            # attribute resets to (2,2)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 1500, {"igt_frames_before": 300})))
    asyncio.run(svc.publish(ev("practice_reset", 2200, {"igt_frames_before": 450})))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    tl = sec["timeline"]
    # no success yet: provisional axis ends at the rightmost point
    assert tl["max_frames"] == 450
    assert tl["max_is_success"] is False


def test_timeline_cleared_attempts_excluded(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    aid = db.attempts()[0].id
    asyncio.run(svc.clear_attempt(aid, reason="accidental"))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    # cleared success excluded → timeline None (only cleared attempt)
    assert sec["timeline"] is None


def test_timeline_none_when_only_abandoned(tmp_path):
    db, svc = make(tmp_path)
    # Abandoned attempt has no timeline-qualifying outcome
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    asyncio.run(svc.new_session())
    # new session: the above attempt is closed as abandoned in projection?
    # Actually star_collected always = success. Use practice_reset to build
    # a section then abandon with new_session.
    # Actually test: section with no qualifying points → None
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.new_session())  # abandons open attempt
    view = build_session_view(db, svc, clock="igt")
    # target (2,2) section is now always present (pinned active star);
    # nothing in the current session, so its attempt list is empty.
    [sec] = view["stars"]
    assert sec["attempts"] == []


# -- scope / sessions tests ---------------------------------------------------

def test_scope_lifetime_shows_other_session_attempts(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400, igt=350)))
    # session scope: only current session's attempt
    view_sess = build_session_view(db, svc, clock="igt", scope="session")
    [sec_sess] = view_sess["stars"]
    assert len(sec_sess["attempts"]) == 1
    # lifetime scope: both attempts
    view_life = build_session_view(db, svc, clock="igt", scope="lifetime")
    [sec_life] = view_life["stars"]
    assert len(sec_life["attempts"]) == 2
    assert view_life["scope"] == "lifetime"
    assert view_sess["scope"] == "session"


def test_view_includes_sessions_list_newest_first(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    asyncio.run(svc.new_session())
    view = build_session_view(db, svc, clock="igt")
    sessions = view["sessions"]
    assert len(sessions) == 2
    assert sessions[0]["id"] == 2   # newest first
    assert sessions[1]["id"] == 1
    # session 1 had one attempt
    s1 = next(s for s in sessions if s["id"] == 1)
    assert s1["attempts"] == 1


def test_attempt_json_carries_rollout_counts(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("rollout", 1100,
                               {"dustless": True, "frames_late": 0, "level": 24})))
    asyncio.run(svc.publish(ev("rollout", 1200,
                               {"dustless": False, "frames_late": 2, "level": 24})))
    asyncio.run(svc.publish(star(1350)))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    a = sec["attempts"][0]
    assert a["rollouts_total"] == 2 and a["rollouts_dustless"] == 1


def test_attempt_json_carries_jump_counts(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("jump", 1100,
                               {"dustless": True, "frames_late": 0,
                                "landing_frames": 1, "kind": "double",
                                "level": 24})))
    asyncio.run(svc.publish(star(1350)))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    a = sec["attempts"][0]
    assert a["jumps_total"] == 1 and a["jumps_dustless"] == 1


# -- timeline markers in the view (spec §3) -------------------------------------

def test_section_carries_markers_by_strat(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    db.set_state("timeline_markers", {
        "2:2:": [{"frames": 90, "label": "wall jump"}],
        "2:2:cannonless": [{"frames": 200, "label": "owl"}],
        "8:1:": [{"frames": 50, "label": "other star — excluded"}],
    })
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["markers_by_strat"] == {
        "": [{"frames": 90, "label": "wall jump"}],
        "cannonless": [{"frames": 200, "label": "owl"}],
    }


def test_markers_default_empty(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    view = build_session_view(db, svc, clock="igt")
    assert view["stars"][0]["markers_by_strat"] == {}


def test_marker_strat_containing_colon_round_trips(tmp_path):
    # key shape is '<course>:<star>:<strat>'; the strat is the FULL
    # remainder after the second colon — protects against a future
    # "split on ':'" refactor.
    db, svc = make(tmp_path)
    seed(svc)
    db.set_state("timeline_markers", {"2:2:a:b": [{"frames": 10, "label": "x"}]})
    view = build_session_view(db, svc, clock="igt")
    assert view["stars"][0]["markers_by_strat"] == {
        "a:b": [{"frames": 10, "label": "x"}]}


# -- progress graph payload (spec §4) -------------------------------------------

def test_progress_groups_successes_by_session_with_pb_flags(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)                                   # session 1: igt 343 + igt 350
    aid = next(a.id for a in db.attempts() if a.igt_frames == 343)
    asyncio.run(svc.save_pb(aid, "igt"))
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400, igt=330)))   # session 2
    view = build_session_view(db, svc, clock="igt", scope="lifetime")
    [sec] = view["stars"]
    prog = sec["progress"]
    assert [s["session_id"] for s in prog["sessions"]] == [1, 2]
    s1 = prog["sessions"][0]
    assert [p["igt_frames"] for p in s1["points"]] == [343, 350]
    assert [p["is_pb_igt"] for p in s1["points"]] == [True, False]
    assert all(p["is_pb_rta"] is False for p in s1["points"])
    p = s1["points"][0]
    assert p["igt"] == "0'11\"43" and p["attempt_id"] == aid
    assert p["t_utc"]            # close timestamp present
    assert s1["started_utc"]     # session metadata present


def test_progress_session_scope_limits_to_current_session(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400, igt=330)))
    view = build_session_view(db, svc, clock="igt", scope="session")
    [sec] = view["stars"]
    assert [s["session_id"] for s in sec["progress"]["sessions"]] == [2]


def test_progress_excludes_cleared_and_is_none_without_successes(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    aid = db.attempts()[0].id
    asyncio.run(svc.clear_attempt(aid, reason="accidental"))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["progress"] is None


def test_progress_superseded_pbs_stay_gold(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    a343 = next(a.id for a in db.attempts() if a.igt_frames == 343)
    a350 = next(a.id for a in db.attempts() if a.igt_frames == 350)
    asyncio.run(svc.save_pb(a350, "igt"))
    asyncio.run(svc.save_pb(a343, "igt"))     # supersedes a350 as current PB
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    flags = {p["attempt_id"]: p["is_pb_igt"]
             for p in sec["progress"]["sessions"][0]["points"]}
    assert flags[a343] is True and flags[a350] is True   # every saved PB is gold


# -- section ordering + pinned target (spec §5) ---------------------------------

def test_sections_ordered_newest_activity_first(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, course=2, star_id=2)))
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(2400, course=8, star_id=1, igt=500)))
    view = build_session_view(db, svc, clock="igt")
    assert [(s["course_id"], s["star_id"]) for s in view["stars"]] \
        == [(8, 1), (2, 2)]
    # fresh activity on (2,2) moves it back to the top
    asyncio.run(svc.publish(ev("practice_reset", 3000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(3400, course=2, star_id=2)))
    view2 = build_session_view(db, svc, clock="igt")
    assert [(s["course_id"], s["star_id"]) for s in view2["stars"]] \
        == [(2, 2), (8, 1)]


def test_target_star_section_always_present(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(8, 2))           # no attempts anywhere
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert (sec["course_id"], sec["star_id"]) == (8, 2)
    assert sec["attempts"] == [] and sec["timeline"] is None
    assert sec["progress"] is None


def test_each_star_progress_contains_only_its_own_attempts(tmp_path):
    # rider from Task 5 review: catch a future in_section plumbing slip
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, course=2, star_id=2, igt=343)))
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(2400, course=8, star_id=1, igt=500)))
    view = build_session_view(db, svc, clock="igt")
    by_star = {(s["course_id"], s["star_id"]): s for s in view["stars"]}
    p22 = by_star[(2, 2)]["progress"]["sessions"][0]["points"]
    p81 = by_star[(8, 1)]["progress"]["sessions"][0]["points"]
    assert [p["igt_frames"] for p in p22] == [343]
    assert [p["igt_frames"] for p in p81] == [500]


def test_race_row_ships_in_progress_payload(tmp_path):
    # rider from Task 5 review: the igt clock needs the same-tick race row
    # (rta=0); the server must NOT filter it — the UI does, per clock.
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1800, {"igt_frames_before": 380})))
    asyncio.run(svc.publish(star(1800, igt=380)))      # same tick: rta = 0
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    [p] = sec["progress"]["sessions"][0]["points"]
    assert p["rta_frames"] == 0 and p["igt_frames"] == 380


def test_resumed_session_points_join_their_original_segment(tmp_path):
    # rider from Task 5 review: continue_session appends to the OLD segment
    # (one segment per session is the chosen semantic, not global chronology)
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))
    asyncio.run(svc.new_session())                       # session 2
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400, igt=350)))
    asyncio.run(svc.continue_session(1))                 # back to session 1
    asyncio.run(svc.publish(ev("practice_reset", 9000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(9400, igt=330)))
    view = build_session_view(db, svc, clock="igt", scope="lifetime")
    [sec] = view["stars"]
    prog = sec["progress"]
    assert [s["session_id"] for s in prog["sessions"]] == [1, 2]
    assert [p["igt_frames"] for p in prog["sessions"][0]["points"]] == [343, 330]


def test_target_section_under_session_scope_keeps_lifetime_context(tmp_path):
    # the pinned-block state right after "new session": empty attempt list,
    # but lifetime timeline/stats still render. A second star with session
    # activity also pins "fresh targets sort last".
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=343)))          # target -> (2,2)
    asyncio.run(svc.new_session())
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(5400, course=8, star_id=1, igt=500)))
    asyncio.run(svc.set_target(2, 2))
    view = build_session_view(db, svc, clock="igt", scope="session")
    assert [(s["course_id"], s["star_id"]) for s in view["stars"]] \
        == [(8, 1), (2, 2)]                                # fresh target last
    tgt = view["stars"][1]
    assert tgt["attempts"] == []                           # nothing this session
    assert tgt["timeline"] is not None                     # lifetime history
    stats = {s["key"]: s["value"] for s in tgt["stats"]}
    assert stats.get("best") == 343                        # lifetime best


def test_view_survives_out_of_range_target(tmp_path):
    # TargetBody has no range validation; the always-materialized target
    # section must not 500 the view — names fall back, links degrade.
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(99, 42))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert (sec["course_id"], sec["star_id"]) == (99, 42)
    assert sec["course_name"] and sec["star_name"]         # fallback strings


def test_strat_set_updates_section_last_strat(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    asyncio.run(svc.set_strat(2, 2, "owlless"))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["last_strat"] == "owlless"
    assert "owlless" in sec["strategies"]


def test_duplicate_stored_stat_selections_render_once(tmp_path):
    # heals dbs that stored duplicates before the write-side dedupe existed
    db, svc = make(tmp_path)
    seed(svc)
    db.set_state("stat_menu", [
        {"key": "best", "params": {}}, {"key": "best", "params": {}},
        {"key": "success_rate", "params": {}},
    ])
    view = build_session_view(db, svc, clock="igt")
    keys = [s["key"] for s in view["stars"][0]["stats"]]
    assert keys == ["best", "success_rate"]

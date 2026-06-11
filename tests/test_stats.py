from sm64_events.stats.registry import REGISTRY, compute_stat, registry_meta
from sm64_events.tracking.projection import Attempt


def attempt(id=1, outcome="success", igt=300, rta=310, cleared=False,
            rollouts=0, dustless=0):
    return Attempt(id=id, session_id=1, course_id=2, star_id=2, strat_tag=None,
                   anchor_type="practice_reset", anchor_frame=0,
                   outcome=outcome, outcome_detail=None,
                   igt_frames=igt, rta_frames=rta,
                   started_utc="2026-06-10T12:00:00Z",
                   ended_utc="2026-06-10T12:00:10Z",
                   cleared=cleared, cleared_reason=None,
                   rollouts_total=rollouts, rollouts_dustless=dustless)


SAMPLE = [
    attempt(1, igt=300), attempt(2, igt=360),
    attempt(3, outcome="reset", igt=120),
    attempt(4, igt=330, cleared=True),         # cleared: excluded everywhere
    attempt(5, outcome="abandoned"),           # excluded from success_rate
    attempt(6, outcome="death", igt=150),      # counts in default failure rate
]


def test_avg_last_n():
    assert compute_stat("avg_last_n", SAMPLE, {"n": 1}, clock="igt") == 360
    assert compute_stat("avg_last_n", SAMPLE, {"n": 10}, clock="igt") == 330


def test_avg_lifetime_best_worst_count():
    assert compute_stat("avg_lifetime", SAMPLE, {}, clock="igt") == 330
    assert compute_stat("best", SAMPLE, {}, clock="igt") == 300
    assert compute_stat("worst", SAMPLE, {}, clock="igt") == 360
    assert compute_stat("success_count", SAMPLE, {}, clock="igt") == 2


def test_clock_selects_rta():
    assert compute_stat("best", SAMPLE, {}, clock="rta") == 310


def test_success_rate_default_failures():
    # 2 successes, 1 reset, 1 death -> 2/4 = 0.5
    assert abs(compute_stat("success_rate", SAMPLE, {}, clock="igt") - 0.5) < 1e-9


def test_success_rate_custom_failure_set():
    # counting nothing as failure -> 1.0
    assert compute_stat("success_rate", SAMPLE, {"failures": []}, clock="igt") == 1.0


def test_empty_inputs_return_none():
    assert compute_stat("best", [], {}, clock="igt") is None
    assert compute_stat("success_rate", [], {}, clock="igt") is None


def test_registry_meta_is_ui_renderable():
    meta = registry_meta()
    keys = {m["key"] for m in meta}
    assert {"avg_last_n", "avg_lifetime", "best", "worst",
            "success_rate", "success_count"} <= keys
    for m in meta:
        assert {"key", "label", "fmt", "params"} <= set(m)


def test_avg_last_n_nonpositive_n_returns_none():
    assert compute_stat("avg_last_n", SAMPLE, {"n": 0}, clock="igt") is None
    assert compute_stat("avg_last_n", SAMPLE, {"n": -5}, clock="igt") is None


def test_unknown_stat_key_fails_loud():
    import pytest
    with pytest.raises(KeyError):
        compute_stat("nonexistent", SAMPLE, {}, clock="igt")


# -- dustless_rate (Phase 2) ---------------------------------------------------

def test_dustless_rate_pools_rollouts_across_attempts():
    attempts = [
        attempt(1, rollouts=3, dustless=2),
        attempt(2, outcome="reset", rollouts=2, dustless=0),  # failures count
        attempt(3, rollouts=4, dustless=4, cleared=True),     # cleared excluded
    ]
    rate = compute_stat("dustless_rate", attempts, {}, clock="igt")
    assert abs(rate - 0.4) < 1e-9   # (2+0) / (3+2)


def test_dustless_rate_none_when_no_rollouts():
    assert compute_stat("dustless_rate", [attempt(1)], {}, clock="igt") is None
    assert compute_stat("dustless_rate", [], {}, clock="igt") is None


def test_dustless_rate_in_registry_meta():
    meta = {m["key"]: m for m in registry_meta()}
    assert meta["dustless_rate"]["fmt"] == "percent"

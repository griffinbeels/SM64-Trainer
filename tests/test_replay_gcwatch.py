"""gen-2 GC policy: the manual collector that stops the disabled-gen-2 leak.

These tests do NOT call arm() — it mutates GLOBAL gc state (freeze / threshold)
and would bleed into every other test. The collector and its decision function
are exercised directly with an injected collect_fn and gc_ops; the real GC
mechanism (freeze survivors, reclaim at a full collection) runs in a child
process for the same reason.
"""
import json
import logging
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

from sm64_events.replay._gcwatch import _Gen2Collector, plan, should_collect

SRC = Path(__file__).resolve().parents[1] / "src"


def fake_gc():
    return SimpleNamespace(get_objects=lambda: [], get_freeze_count=lambda: 0,
                           freeze=lambda: None, unfreeze=lambda: None)


def test_should_collect_when_idle():
    assert should_collect(idle=True, secs_since_collect=0.0, force_after_s=300)


def test_should_collect_forces_after_interval_even_when_active():
    assert not should_collect(False, 10.0, 300.0)      # active, recent -> skip
    assert should_collect(False, 300.0, 300.0)         # active, stale -> force


def test_plan_collects_everything_hourly_and_survivors_every_five_minutes():
    assert plan(False, 10.0, 10.0, 300.0, 3600.0, 600.0) is None           # active, recent
    assert plan(False, 300.0, 600.0, 300.0, 3600.0, 600.0) == "survivors"  # 5-minute backstop
    assert plan(False, 300.0, 3600.0, 300.0, 3600.0, 600.0) == "full"      # never idle: hourly full


def test_idle_shortens_the_full_backstop_instead_of_collecting_every_poll():
    """2026-09-24: a full collection every 30 s poll while capture was
    retired, 153-188 ms each for a few hundred objects. Retired includes
    GPU reconnect gaps during play, so idle only brings the full one forward
    to every 10 minutes."""
    assert plan(True, 30.0, 30.0, 300.0, 3600.0, 600.0) is None          # idle, full was recent
    assert plan(True, 599.0, 599.0, 300.0, 3600.0, 600.0) == "survivors"  # still only the backstop
    assert plan(True, 30.0, 600.0, 300.0, 3600.0, 600.0) == "full"        # idle and 10 min since full
    assert plan(False, 30.0, 600.0, 300.0, 3600.0, 600.0) is None         # the same moment, not idle


def test_an_idle_hour_runs_at_most_six_full_collections():
    """The real loop over an hour of Project64-closed polling (the acceptance
    was about 120 an hour before, 6 or fewer after). Every clock read is one
    30 s poll later, so the hour passes in milliseconds."""
    now = [0.0]
    hour_over = threading.Event()

    def clock():
        now[0] += 30.0
        if now[0] > 3600.0:
            hour_over.set()
        return now[0]

    fulls = []
    col = _Gen2Collector(is_idle=lambda: True, poll_s=0.001, collect_fn=lambda: 0,
                         clock=clock, gc_ops=SimpleNamespace(
                             get_objects=lambda: [], get_freeze_count=lambda: 0,
                             freeze=lambda: None,
                             unfreeze=lambda: fulls.append(now[0])))  # only a full unfreezes
    col.start()
    try:
        assert hour_over.wait(timeout=5.0)
    finally:
        col.stop()
    within_hour = [at for at in fulls if at <= 3600.0]
    assert 1 <= len(within_hour) <= 6, within_hour


def test_the_shipped_backstops_are_ordered():
    col = _Gen2Collector(is_idle=lambda: True, gc_ops=fake_gc())
    assert col._poll_s < col._force_after_s <= col._idle_full_after_s < col._full_after_s


def test_every_manual_collection_logs_its_kind_walk_and_pause(caplog):
    col = _Gen2Collector(is_idle=lambda: False, collect_fn=lambda: 3, gc_ops=SimpleNamespace(
        get_objects=lambda: [1] * 5, get_freeze_count=lambda: 7,
        freeze=lambda: None, unfreeze=lambda: None))
    with caplog.at_level(logging.INFO, logger="sm64.replay"):
        col.run("survivors")
        col.run("full")
    lines = [r.getMessage() for r in caplog.records if "gc gen2" in r.getMessage()]
    assert lines[0].startswith("gc gen2 survivors collect: walked 5 containers in ")
    assert lines[1].startswith("gc gen2 full collect: walked 12 containers in ")
    assert all("ms, reclaimed 3" in line for line in lines)


def test_collector_runs_collection_while_idle():
    """Idle is the free moment to collect — the loop must actually fire
    collect_fn (this is the bug that caused the leak: nothing ever did)."""
    fired = threading.Event()
    calls = []

    def fake_collect():
        calls.append(1)
        fired.set()
        return 7  # pretend it reclaimed something

    col = _Gen2Collector(is_idle=lambda: True, poll_s=0.01, force_after_s=999,
                         idle_full_after_s=0.0, collect_fn=fake_collect, gc_ops=fake_gc())
    col.start()
    try:
        assert fired.wait(timeout=2.0)                 # collected while idle
    finally:
        col.stop()
    assert len(calls) >= 1 and col.last[0] == "full"


def test_collector_idle_callable_is_honored():
    """When never idle and the force interval is far off, no collection
    happens — the collector must not collect unconditionally."""
    calls = []
    col = _Gen2Collector(is_idle=lambda: False, poll_s=0.01,
                         force_after_s=999, collect_fn=lambda: calls.append(1), gc_ops=fake_gc())
    col.start()
    try:
        threading.Event().wait(0.1)                    # several poll cycles
    finally:
        col.stop()
    assert calls == []                                 # active + not stale -> no collect


CHILD = textwrap.dedent("""
    import gc, json, sys, time, weakref
    sys.path.insert(0, sys.argv[1])
    from sm64_events.replay._gcwatch import _Gen2Collector

    gc.collect(); gc.freeze()                  # arm(): the startup heap is frozen
    g0, g1, _ = gc.get_threshold(); gc.set_threshold(g0, g1, 1_000_000)
    col = _Gen2Collector(is_idle=lambda: False)
    kept, walked, pauses = [], [], []
    for _ in range(8):                          # survivors pile up between backstops
        kept.append([{"i": i, "v": [i]} for i in range(60_000)])
        col.run("survivors")
        walked.append(col.last[1]); pauses.append(col.last[2])

    class Node:
        pass
    node = Node(); node.me = node               # a cycle that survives one collection
    alive = weakref.ref(node)
    col.run("survivors")                        # ... and is frozen with its survivors
    del node
    col.run("survivors")
    after_survivors = alive() is not None
    col.run("full")                             # idle or hourly: unfreeze and collect all
    print(json.dumps(dict(walked=walked, pauses=pauses, cycle_after_survivors=after_survivors,
                          cycle_after_full=alive() is not None)))
""")


def test_backstop_walks_only_new_survivors_and_a_full_collection_reclaims_frozen_cycles():
    """Real GC in a child process. Each backstop walks only what arrived since
    the previous one (flat as survivors accumulate: 120k containers a round,
    960k by the end), and a cycle that dies after being frozen is reclaimed
    only by a full collection (the idle path or the hourly backstop)."""
    result = subprocess.run([sys.executable, "-c", CHILD, str(SRC)], capture_output=True,
                            text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    print(json.dumps(report))
    # Flatness is asserted on containers walked, which is deterministic; pause
    # ms is wall-clock noise under a loaded merge check (a 3x+5 ms bound
    # flaked at 3.5..17.5 ms), so it only gets a loose sanity ceiling.
    walked = report["walked"][1:]
    assert max(walked) < 1.2 * min(walked), walked  # flat: only each round's arrivals
    assert walked[-1] < 200_000 and sum(report["walked"]) > 900_000  # never the whole pile
    assert all(ms < 200 for ms in report["pauses"]), report["pauses"]
    assert report["cycle_after_survivors"] and not report["cycle_after_full"]

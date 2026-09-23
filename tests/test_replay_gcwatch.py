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


def test_plan_collects_everything_when_idle_or_hourly_and_survivors_otherwise():
    assert plan(True, 0.0, 0.0, 300.0, 3600.0) == "full"            # capture idle: free moment
    assert plan(False, 10.0, 10.0, 300.0, 3600.0) is None           # active, recent
    assert plan(False, 300.0, 600.0, 300.0, 3600.0) == "survivors"  # 5-minute backstop
    assert plan(False, 300.0, 3600.0, 300.0, 3600.0) == "full"      # never idle: hourly full


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

    col = _Gen2Collector(is_idle=lambda: True, poll_s=0.01,
                         force_after_s=999, collect_fn=fake_collect, gc_ops=fake_gc())
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
    walked = report["walked"]
    assert all(value < 1.2 * walked[1] for value in walked[1:]), walked  # never the whole pile
    assert walked[-1] < 200_000 and sum(walked) > 900_000
    assert max(report["pauses"][1:]) < 3 * min(report["pauses"][1:]) + 5  # ms, flat
    assert report["cycle_after_survivors"] and not report["cycle_after_full"]

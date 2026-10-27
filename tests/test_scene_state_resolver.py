"""The free camera's state resolvers (tools/scene_lab/state_resolver.py): scene states resolved in child processes.

Real children run state_resolver.serve over test_scene_state_package.py's synthetic package, with
the stand-in evaluator scene_resolver_double.py in place of the game's code; the server's half is
the real Resolvers and the real evaluator.Evaluator, its geo code (e2_georender, which needs
Unicorn) stood in for. That a child's state is byte for byte the one the server resolves itself,
and the pictures with it, was measured on a real recording with Unicorn and the GPU, outside the suite.
"""
import importlib.util
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
sys.path.insert(0, str(ROOT / "tests"))
import scene_resolver_double as double  # noqa: E402
import state_resolver  # noqa: E402

spec = importlib.util.spec_from_file_location("scene_state_synthetic", ROOT / "tests/test_scene_state_package.py")
synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synthetic)


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    """The synthetic scene-state package (fourteen frames) and its ROM on disk, as a child reads it."""
    folder = tmp_path_factory.mktemp("resolver")
    rom = synthetic._rom()
    out = folder / "synthetic.sm64scene"
    synthetic.pk.write_package(synthetic.pk.load_source(synthetic._session(folder, rom), states=True), rom, out,
                               synthetic.pk.ZLIB, group=3, checkpoint_every=synthetic.EVERY)
    (folder / "rom.z64").write_bytes(rom)
    return {"data": out.read_bytes(), "rom": rom, "rom_path": folder / "rom.z64"}


class Deliveries:
    """deliver(): every state the children hand back, in order, and a wait for some of them."""

    def __init__(self):
        self.got, self.lock = [], threading.Condition()

    def __call__(self, index, answer, ram):
        with self.lock:
            self.got.append((index, answer, ram))
            self.lock.notify_all()

    def wait(self, indices, seconds=20.0):
        deadline = time.monotonic() + seconds
        with self.lock:
            while not set(indices) <= {index for index, _a, _r in self.got}:
                assert self.lock.wait(max(0.0, deadline - time.monotonic())), f"waited for {indices}: {self.got}"
        return {index: (answer, ram) for index, answer, ram in self.got}

    @property
    def order(self):
        return [index for index, _a, _r in self.got]


def _pool(scene, deliver, evaluator="FakeStates", count=2):
    resolvers = state_resolver.Resolvers(scene["data"], scene["rom_path"], scene["rom_path"], deliver, count=count,
                                         evaluator=f"scene_resolver_double:{evaluator}", extra_path=(ROOT / "tests",))
    deadline = time.monotonic() + 30
    while resolvers.ready < count and time.monotonic() < deadline:
        time.sleep(0.02)
    assert resolvers.ready == count
    return resolvers


def test_frames_handed_ahead_come_back_from_below_normal_children_and_close_ends_them(scene):
    deliveries = Deliveries()
    resolvers = _pool(scene, deliveries)
    try:
        assert resolvers.ahead([4, 5, 6, 7, double.NO_SCENE])
        got = deliveries.wait([4, 5, 6, 7, double.NO_SCENE])
        package = synthetic.pk.Package(scene["data"], scene["rom"])
        for index in (4, 5, 6, 7):
            answer, ram = got[index]
            assert ram == double.ram_of(index) and answer["status"] is None and answer["words"] == list(range(16))
            assert answer["digests"] == [package.index.frames[index][4].hex(), package.states.rows[index][4].hex()]
            assert answer["result"]["run"]["head"] == 0x80300100 + index and answer["restored"] == [[0x1000, 0x1010]]
        answer, ram = got[double.NO_SCENE]
        assert answer["status"] == "no scene" and ram is None
        assert sorted(deliveries.order) == [double.NO_SCENE, 4, 5, 6, 7]          # each frame once
        if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
            assert {psutil.Process(pid).nice() for pid in resolvers.pids} == {psutil.BELOW_NORMAL_PRIORITY_CLASS}
    finally:
        resolvers.close()
    assert all(child.process.poll() is not None for child in resolvers.children)
    with pytest.raises(FileNotFoundError):
        state_resolver.shared_memory.SharedMemory(name=resolvers.memory.name)


def test_while_play_runs_ahead_of_the_children_one_child_goes_on_from_its_own_frames(scene):
    """Each frame's state needs the frame before it assembled: the child that has it takes the next."""
    deliveries = Deliveries()
    resolvers = _pool(scene, deliveries)
    try:
        for index in range(4, 12):
            resolvers.ahead([index])
            deliveries.wait([index])
        children = {deliveries.wait([index])[index][0]["entry"]["pool"] for index in range(4, 12)}
        assert len(children) == 1, children
    finally:
        resolvers.close()


def test_a_frame_waited_for_goes_next_and_the_frames_queued_before_it_are_dropped(scene):
    deliveries = Deliveries()
    resolvers = _pool(scene, deliveries, "SlowStates")
    try:
        resolvers.ahead([4, 5, 6, 7, 8, 9])
        deadline = time.monotonic() + 10
        while len(resolvers.running) < 2 and time.monotonic() < deadline:   # both children on a frame
            time.sleep(0.005)
        running = set(resolvers.running)
        assert resolvers.take(8)
        deliveries.wait([8, 9])
        assert set(deliveries.order) == running | {8, 9}             # the frames before 8 never ran
        assert deliveries.order.index(8) < deliveries.order.index(9)
    finally:
        resolvers.close()


def test_a_frame_no_child_can_resolve_is_left_to_the_caller_and_so_is_everything_once_they_end(scene):
    deliveries = Deliveries()
    resolvers = _pool(scene, deliveries)
    try:
        resolvers.ahead([double.FAILS])
        assert deliveries.wait([double.FAILS])[double.FAILS] == (None, None)
        assert not resolvers.take(double.FAILS)
        assert resolvers.take(5)                                      # the others still resolve
        deliveries.wait([5])
        for pid in resolvers.pids:
            psutil.Process(pid).kill()
        resolvers.ahead([6, 7])                                       # a child that ended is found on its next frame
        got = deliveries.wait([6, 7])
        assert got[6] == got[7] == (None, None) and resolvers.alive == 0
        assert not resolvers.take(8) and not resolvers.ahead([8, 9])
    finally:
        resolvers.close()


# ---------------------------------------------------------------- the server's evaluator

class GeoStandIn:
    """evaluator.py's view of e2_georender, with the geo code replaced: resolve_task records
    the frames resolved in this process."""

    def __init__(self):
        self.resolved_here = []
        self.US = {"gGlobalTimer": 0x80320000}
        self.phys = lambda address: address & 0x1FFFFFFF
        self.Cpu = lambda rom, fcr31: None
        self.code_images = lambda rom, windows: (np.zeros(4, np.uint8), np.zeros(4, bool), np.zeros(4, bool))
        self.TapFrame = lambda source, task, ram: SimpleNamespace(task_words=tuple(task.task), ram=ram)
        self.Resolved = lambda *fields: SimpleNamespace(**dict(zip(
            ("entry", "known", "status", "frame", "layout", "result", "notes", "restored", "pointers", "recorded"),
            fields, strict=False)))

    def resolve_task(self, frame, cpu, known, symbols, previous, recorded):
        self.resolved_here.append(frame.ram[:4])
        return SimpleNamespace(status=None, frame=SimpleNamespace(ram=b"here", task_words=frame.task_words),
                               recorded=False, entry={}, layout={}, result={"run": {"head": 0}})


@pytest.fixture
def evaluator(scene, monkeypatch):
    geo = GeoStandIn()
    monkeypatch.setitem(sys.modules, "e2_georender", SimpleNamespace(**vars(geo), resolve_task=geo.resolve_task))
    module = importlib.import_module("evaluator")
    made = module.Evaluator(synthetic.pk.Package(scene["data"], scene["rom"]), scene["rom"], [], source=Path("x"))
    made.geo = geo
    return made


def test_the_evaluator_draws_from_a_childs_state_and_resolves_here_only_what_no_child_can(scene, evaluator):
    resolvers = _pool(scene, evaluator.delivered)
    evaluator.attach(resolvers, 6)
    try:
        assert evaluator.ahead([4, 5, 6])
        for index in (4, 5, 6, 7):                                     # 7 was never handed ahead: taken now
            assert evaluator.state(index).frame.ram == double.ram_of(index)
        assert evaluator.geo.resolved_here == []
        assert evaluator.state(double.FAILS).frame.ram == b"here"      # the child could not: resolved here
        assert len(evaluator.geo.resolved_here) == 1
        assert evaluator.recorded["absent"] == 5
    finally:
        resolvers.close()


def test_states_from_another_package_are_refused_and_the_children_closed(scene, evaluator):
    resolvers = _pool(scene, evaluator.delivered, "WrongPackage")
    evaluator.attach(resolvers, 6)
    try:
        assert evaluator.state(5).frame.ram == b"here"
        assert evaluator.resolvers is None and resolvers.closed
        assert not evaluator.ahead([6, 7])
    finally:
        resolvers.close()

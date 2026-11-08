"""The suite's fence between tests and his real user data (tests/conftest.py::_fence_user_data).

2026-11-08: a fake slot from this suite sat in his real Slot Library as a captured state. These tests hold the
fence: every per-user root lands in the test's own folder, the door that leaked keeps its default in the fence,
a test that names its own LocalAppData keeps it, and the named opt-out steps the fence aside.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from sm64_events.core import paths

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
REAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def test_every_per_user_root_lands_in_this_tests_own_folder(tmp_path):
    import attempt_scenes
    import fixture
    import handoff_try
    import replay
    import tap
    fence = tmp_path / "localappdata"
    roots = {"user data": paths.user_data_root(), "slot library": paths.slot_library_dir(),
             "scene lab": paths.scene_lab_root(), "installation record": paths.shared_installation_root(),
             "tap lab": tap.lab_root(), "replay lab": replay.default_lab_root(),
             "attempt scenes lab": attempt_scenes.default_lab_root(), "slot backups": handoff_try.backups_folder(),
             "plugin backup": fixture.backup_dir()}
    outside = {name: str(root) for name, root in roots.items() if not root.is_relative_to(fence)}
    assert outside == {}


def test_the_door_that_leaked_keeps_its_default_library_in_the_fence(tmp_path):
    """SceneLab.follow_spawns without a library means his library: under the fence, the test's own."""
    from sm64_events.server import scene_lab_api

    class Broadcaster:
        def observe(self, _observer):
            pass

    class Tracker:
        broadcaster = Broadcaster()
    lab = scene_lab_api.SceneLab.__new__(scene_lab_api.SceneLab)
    lab.follow_spawns(Tracker(), game=lambda: None, log_file=tmp_path / "refit.jsonl")
    assert lab.refitter.library.root.is_relative_to(tmp_path / "localappdata")


def test_a_test_that_names_its_own_local_app_data_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "mine"))
    assert paths.slot_library_dir() == tmp_path / "mine" / "SM64Trainer" / "slot-library"


def test_an_inherited_practice_profile_cannot_aim_a_test_at_his_profile():
    assert os.environ.get("SM64_PRACTICE_ROOT") is None and paths.practice_root() is None


@pytest.mark.real_user_data(reason="proves the fence steps aside when asked; reads a path and writes nothing")
def test_the_named_opt_out_sees_the_real_root():
    assert paths.user_data_root() == REAL / "SM64Trainer"

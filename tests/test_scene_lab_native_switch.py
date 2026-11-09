"""Each Ghost Lab native's build function: the installed app's own copy, or this checkout's build.

The installed app has no compiler (core/update_plan.py LAB_NATIVES ship in
_internal/sm64_events/data/lab, found by core/paths.py bundled_lab_file), so
frozen, every build function returns the bundled file and never compiles; a
missing one is a plain sentence, or the Python fallback where one exists. From
a checkout each still compiles into the scene lab's build cache, through the
one recipe in tools/build_lab.py. Nothing here compiles or starts a host.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from sm64_events.core import paths

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import cpu_hooks  # noqa: E402
import ghost_sound  # noqa: E402
import stems_live  # noqa: E402
import tap  # noqa: E402
import viewer  # noqa: E402

# (the lab module, its build function, the native it answers with)
BUILDS = [(viewer, "build", "viewer_host.exe"), (tap, "build_offline_host", "tap_offline_host.exe"),
          (ghost_sound, "build", "ghost_sound_host.exe"), (cpu_hooks, "build", "scene_cpu_hooks.dll"),
          (stems_live, "build_dll", "scene_cxd4.dll")]
IDS = [name for _, _, name in BUILDS]


def answer(module, function):
    """The function's path (stems_live answers (path, why))."""
    result = getattr(module, function)()
    return result[0] if module is stems_live else result


def test_the_build_functions_cover_the_registry():
    from sm64_events.core.update_plan import LAB_NATIVES
    assert sorted(IDS) == sorted(LAB_NATIVES)


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    def no_compiler(*args, **kwargs):
        raise AssertionError(f"the installed app reached for a build tool: {args}")
    monkeypatch.setattr(importlib.util, "spec_from_file_location", no_compiler)
    return monkeypatch


@pytest.mark.parametrize("module, function, name", BUILDS, ids=IDS)
def test_frozen_answers_the_bundled_file_and_never_compiles(frozen, tmp_path, module, function, name):
    bundled = tmp_path / "sm64_events" / "data" / "lab" / name
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"MZ bundled")
    asked = []
    frozen.setattr(paths, "bundled_lab_file", lambda wanted: asked.append(wanted) or bundled, raising=False)
    assert answer(module, function) == bundled and asked == [name]


@pytest.mark.parametrize("module, function, name", BUILDS, ids=IDS)
def test_frozen_without_the_file_says_so_in_a_sentence_or_falls_back(frozen, module, function, name):
    frozen.setattr(paths, "bundled_lab_file", lambda wanted: None, raising=False)
    if module is cpu_hooks:
        assert cpu_hooks.build() is None            # the Python hooks run instead
    elif module is stems_live:
        dll, why = stems_live.build_dll()
        assert dll is None and name in why and why.endswith("Reinstall SM64 Trainer.")
    else:
        with pytest.raises(RuntimeError, match=rf"missing {name}, .*\. Reinstall SM64 Trainer\.$"):
            getattr(module, function)()


@pytest.mark.parametrize("module, function, name", BUILDS, ids=IDS)
def test_a_checkout_builds_through_the_one_recipe(monkeypatch, tmp_path, module, function, name):
    """Not frozen: each function asks tools/build_lab.py for its native (here a stand-in recipe module)."""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    stand_in = tmp_path / "build_lab_stand_in.py"
    stand_in.write_text("from pathlib import Path\n"
                        "class ToolchainMissing(RuntimeError):\n    pass\n"
                        "ASKED = []\n"
                        "def cached(name, folder=None):\n"
                        "    ASKED.append((name, folder))\n"
                        "    return Path('cache') / name\n", encoding="utf-8")
    real = importlib.util.spec_from_file_location
    loaded = []

    def recipe(spec_name, location, *args, **kwargs):
        assert Path(location).resolve() == (ROOT / "tools" / "build_lab.py").resolve()
        loaded.append(spec_name)
        return real(spec_name, stand_in)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", recipe)
    assert answer(module, function) == Path("cache") / name
    assert loaded, "the build function never loaded tools/build_lab.py"

"""The launcher, wrap and release keep the native bundle equal to the sources.

tools/build_natives.py with fake natives: an up-to-date file is left alone, an
out-of-date one comes from the shared cache or is compiled once, and one that
cannot be built leaves the bundle rather than staying stale.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("build_natives_under_test", ROOT / "tools/build_natives.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_id(n):
    return f"{n:064x}-fake"


@pytest.fixture
def natives(tmp_path, monkeypatch):
    module = load()
    monkeypatch.setattr(module, "BUNDLE", tmp_path / "bundle")
    monkeypatch.setattr(module, "cache_root", lambda: tmp_path / "cache")
    world = {"source": 1, "compiles": 0, "toolchain": True}

    def build(out):
        if not world["toolchain"]:
            raise RuntimeError("no x86 MSVC toolchain (vcvars32.bat) found")
        world["compiles"] += 1
        out.mkdir(parents=True, exist_ok=True)
        (out / "fake.dll").write_bytes(b"MZ" + build_id(world["source"]).encode() + b"\0" * 64)
        return out / "fake.dll"

    fake = module.Native("fake.dll", lambda: build_id(world["source"]), build, "the fake toolchain")
    monkeypatch.setattr(module, "natives", lambda: (fake,))
    return module, world, tmp_path / "bundle" / "fake.dll"


def test_a_fresh_checkout_builds_once_and_a_second_checkout_copies(natives, tmp_path, monkeypatch):
    module, world, target = natives
    assert module.ensure(log=lambda _: None) == {"fake.dll": "built"}
    assert world["compiles"] == 1 and module.embedded_id(target) == build_id(1)
    assert module.ensure(log=lambda _: None) == {"fake.dll": "fresh"}
    monkeypatch.setattr(module, "BUNDLE", tmp_path / "another checkout")
    assert module.ensure(log=lambda _: None) == {"fake.dll": "built"}
    assert world["compiles"] == 1, "the same sources in another checkout are a cache copy"


def test_a_source_edit_rebuilds_and_the_old_build_stays_cached(natives):
    module, world, target = natives
    module.ensure(log=lambda _: None)
    world["source"] = 2
    assert module.ensure(build=False, log=lambda _: None) == {"fake.dll": "stale"}
    module.ensure(log=lambda _: None)
    assert world["compiles"] == 2 and module.embedded_id(target) == build_id(2)
    world["source"] = 1  # back on the first branch: no compile
    module.ensure(log=lambda _: None)
    assert world["compiles"] == 2 and module.embedded_id(target) == build_id(1)


def test_without_a_toolchain_a_stale_native_leaves_the_bundle(natives):
    """The setup screen then says unavailable instead of offering old code."""
    module, world, target = natives
    module.ensure(log=lambda _: None)
    world["source"], world["toolchain"] = 2, False
    said = []
    assert module.ensure(log=said.append) == {"fake.dll": "missing"}
    assert not target.exists()
    assert any("needs the fake toolchain" in line and "no x86 MSVC toolchain" in line for line in said)
    with pytest.raises(RuntimeError, match="could not be built"):
        module.ensure(strict=True, log=lambda _: None)


def test_without_a_toolchain_a_build_another_checkout_made_still_installs(natives, tmp_path, monkeypatch):
    module, world, _ = natives
    module.ensure(log=lambda _: None)
    world["toolchain"] = False
    monkeypatch.setattr(module, "BUNDLE", tmp_path / "fresh clone")
    assert module.ensure(log=lambda _: None) == {"fake.dll": "built"}


def test_nothing_under_the_bundle_folder_is_tracked():
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "src/sm64_events/data/plugin"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    assert tracked == [], f"build outputs are committed again: {tracked}"

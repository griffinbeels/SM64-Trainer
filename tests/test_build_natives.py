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
    world["scanned"] = []
    monkeypatch.setattr(module, "scan", lambda paths, log: world["scanned"].append(list(paths)))
    world["data_scanned"] = []
    monkeypatch.setattr(module, "model_data_scan", lambda paths: world["data_scanned"].append(list(paths)))
    return module, world, tmp_path / "bundle" / "fake.dll"


def test_a_native_holding_model_data_is_never_cached_or_installed(natives, monkeypatch):
    """Every compiled native passes the model data scan before the cache keeps it."""
    module, world, target = natives

    def refuse(paths):
        raise RuntimeError(f"{Path(paths[0]).name} holds Nintendo model data")
    monkeypatch.setattr(module, "model_data_scan", refuse)
    lines = []
    assert module.ensure(log=lines.append) == {"fake.dll": "missing"}
    assert not target.exists() and any("holds Nintendo model data" in line for line in lines)
    assert not any((module.cache_root() / "fake.dll").glob("*/fake.dll")), "the flagged build stays out of the cache"
    monkeypatch.setattr(module, "model_data_scan", lambda paths: world["data_scanned"].append(list(paths)))
    assert module.ensure(log=lambda _: None) == {"fake.dll": "built"}
    assert world["compiles"] == 2 and len(world["data_scanned"]) == 1


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


def test_every_native_a_run_installs_is_scanned_and_a_fresh_one_is_not(natives):
    module, world, target = natives
    module.ensure(log=lambda _: None)
    assert world["scanned"] == [[target]]
    module.ensure(log=lambda _: None)
    assert world["scanned"] == [[target]], "a fresh bundle is not rescanned at every launch"


def test_a_threat_stops_the_run_naming_the_file_and_removes_it_from_bundle_and_cache(natives, monkeypatch):
    """2026-11-06: Defender quarantined our renderer as Trojan:Win32/Bearfoos.A!ml.
    Builds are repeatable, so a flagged build must not stay cached for the next run."""
    module, world, target = natives
    scanner = module._load("defender_scan")

    def flag(paths, log):
        return scanner.require_clean(paths, log=log, runner=lambda command: (
            2, "Threat                  : Trojan:Win32/Bearfoos.A!ml\nfound 1 threats"))
    monkeypatch.setattr(module, "scan", flag)
    said = []
    with pytest.raises(scanner.ThreatFound) as stopped:
        module.ensure(log=said.append)   # not strict: a threat stops every run
    assert str(target) in str(stopped.value) and "Bearfoos" in str(stopped.value)
    assert any(line.startswith("  STOP:") and "fake.dll" in line for line in said)
    assert not target.exists()
    assert not any((module.cache_root() / "fake.dll").glob("*/fake.dll")), "the flagged build stayed cached"
    monkeypatch.setattr(module, "scan", lambda paths, log: None)
    assert module.ensure(log=lambda _: None) == {"fake.dll": "built"}
    assert world["compiles"] == 2, "the next run builds it again instead of copying the flagged bytes"


def test_a_scan_that_cannot_run_is_reported_and_the_build_continues(natives, monkeypatch):
    module, _, target = natives
    scanner = module._load("defender_scan")
    monkeypatch.setattr(module, "scan", lambda paths, log: scanner.require_clean(
        paths, log=log, runner=lambda command: (1, "scan engine unavailable")))
    said = []
    assert module.ensure(log=said.append) == {"fake.dll": "built"}
    assert target.is_file() and any("could not scan" in line for line in said)


def test_the_bundle_is_the_one_plugin_and_the_helper():
    """One graphics plugin file since 2026-11-07 (tools/build_renderer.py links
    the capture layer into the renderer): no second renderer DLL is built."""
    module = load()
    assert [native.name for native in module.natives()] == ["sm64_trainer_gfx.dll", "SM64GpuEncoderV1.dll"]
    assert module.RETIRED == ("GLideN64_SM64Trainer.dll",)


def test_a_retired_renderer_leaves_the_bundle_so_nothing_installs_it(natives):
    """A checkout built before 2026-11-07 still holds GLideN64_SM64Trainer.dll in
    its bundle; a building run removes it (a check-only run leaves the folder be)."""
    module, _, target = natives
    retired = target.parent / "GLideN64_SM64Trainer.dll"
    retired.parent.mkdir(parents=True)
    retired.write_bytes(b"MZ an older renderer")
    module.ensure(build=False, log=lambda _: None)
    assert retired.is_file()
    said = []
    assert module.ensure(log=said.append) == {"fake.dll": "built"}
    assert not retired.exists() and any("removed GLideN64_SM64Trainer.dll" in line for line in said)


def test_nothing_under_the_bundle_folder_is_tracked():
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "src/sm64_events/data/plugin"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    assert tracked == [], f"build outputs are committed again: {tracked}"

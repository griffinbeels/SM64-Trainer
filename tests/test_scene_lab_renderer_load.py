"""The scene lab's hosts load THE RENDERER where the Trainer keeps it, never a copy.

On 2026-11-06 Defender's cloud model flagged the scene viewer's host (viewer_host.exe, built on
this machine) opening a copy of the renderer it had just written into a random %TEMP% folder:
how a dropper behaves. Now both hosts (tools/scene_lab/viewer_host.c, replay_host.c, through
renderer_load.h) load the Trainer's graphics plugin (one file since 2026-11-07, the renderer
inside it) by its full path, run it as the renderer alone (SM64RendererOnlyV1: no control page,
no capture, no plugin log) and name the renderer settings folder through the renderer's own
setter (plugin/gfxwrap/renderer_settings_folder.cpp), all before any plugin call.

The native half runs the REAL built hosts over a test plugin: the REAL capture layer linked with
a stand-in renderer (tests/native/settings_folder_renderer.cpp) and the REAL setter: no GL, no
GPU, no Project64. The Python half checks who decides what the hosts load
(replay.trainer_renderer, settings_folder, scene_session.renderer_host) without starting anything.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.core import paths
from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import replay  # noqa: E402
import scene_session  # noqa: E402
import viewer  # noqa: E402

HEADER = "80371240" + "00" * 60          # any 64-byte ROM header: the stand-in never reads it


def _settings_files(plugin: Path, store: str = "") -> None:
    """Project64's two renderer files; `store` the shader store line, if GLideN64.ini has one."""
    plugin.mkdir(parents=True)
    (plugin / "GLideN64.ini").write_text(
        "[General]\nversion=26\nprofile=User\n\n[User]\nvideo\\windowedWidth=640\nvideo\\windowedHeight=480\n"
        f"textureFilter\\txPath=C:/Project64/Plugin/hires_texture\n{store}"
        "frameBufferEmulation\\enable=0\n", encoding="utf-8")
    (plugin / "GLideN64.custom.ini").write_text(
        "[SUPER%20MARIO%2064]\nvideo\\windowedWidth=800\nvideo\\windowedHeight=600\n"
        "generalEmulation\\enableShadersStorage=1\n", encoding="utf-8")


# ---------------------------------------------------------------- the real hosts


@pytest.fixture(scope="module")
def stand_ins(tmp_path_factory):
    """The stand-in renderer twice, each in a folder of its own (its bundle): inside a test
    plugin (the real capture layer, so the real SM64RendererOnlyV1, and the real settings-folder
    setter), and as a DLL of its own with neither (LINK's own renderer, or an older Trainer build
    whose renderer was a second file)."""
    if sys.platform != "win32":
        pytest.skip("Win32 hosts")
    sys.path.insert(0, str(ROOT / "tools"))
    import build_plugin
    vcvars = build_plugin.find_vcvars32()
    assert vcvars, "the x86 MSVC toolchain is required to build the scene-lab hosts"
    root = tmp_path_factory.mktemp("stand-ins")
    stand_in = ROOT / "tests/native/settings_folder_renderer.cpp"
    trainer = build_plugin.build_test_plugin(root / "trainer", (stand_in, "renderer_settings_folder.cpp"), vcvars)
    work = root / "stock"
    work.mkdir()
    stock = work / "GLideN64.dll"
    flags = [flag for flag in build_plugin.COMMON_FLAGS if not flag.startswith("/std:")]
    build_plugin._cl(vcvars, [*flags, "/std:c++17", "/EHsc", "/LD", "/DSTANDALONE", f"/I{ROOT / 'plugin/gfxwrap'}",
                              str(stand_in), f"/Fe:{stock}", f"/Fo{work}\\",
                              "/link", f"/IMPLIB:{work / 'renderer.lib'}", "user32.lib", "kernel32.lib"], work)
    return {"hosts": {"viewer": viewer.build(), "replay": replay.build()}, "trainer": trainer, "stock": stock}


def _run(command: list[str], tmp_path: Path, stdin: bytes = b"") -> tuple[subprocess.CompletedProcess, list[str]]:
    """One host run, its stand-in's log, and a private temp folder: a host that wrote a file
    to its temp folder would leave it there."""
    log, temp = tmp_path / "renderer.log", tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    env = {**os.environ, "SM64_FAKE_RENDERER_LOG": str(log), "TEMP": str(temp), "TMP": str(temp)}
    result = subprocess.run(command, input=stdin, capture_output=True, timeout=60, env=env, check=False,
                            **quiet_spawn_kwargs())
    lines = log.read_text(encoding="utf-8-sig").splitlines() if log.is_file() else []
    return result, lines


def _frame(folder: Path) -> Path:
    """One replay_host frame input (savestate_frames.py's format), all zero."""
    folder.mkdir(parents=True)
    (folder / "rdram.bin").write_bytes(bytes(4096))
    (folder / "dmem.bin").write_bytes(bytes(0x1000))
    vi = ", ".join(f'"{name}": 0' for name in ("status", "origin", "width", "intr", "v_current", "burst", "v_sync",
                                               "h_sync", "leap", "h_start", "v_start", "v_burst", "x_scale",
                                               "y_scale"))
    (folder / "frame.json").write_text(
        f'{{"schema": "scene-lab-frame/1", "vi": {{{vi}}}, "rom_header": "{HEADER}"}}', encoding="utf-8")
    return folder


def _commands(stand_ins, renderer: Path, settings: Path, tmp_path: Path) -> dict[str, list[str]]:
    return {"viewer": [str(stand_ins["hosts"]["viewer"]), "--renderer", str(renderer), "--settings", str(settings),
                       "--header", HEADER],
            "replay": [str(stand_ins["hosts"]["replay"]), "--renderer", str(renderer), "--settings", str(settings),
                       "--out", str(tmp_path / "out"), str(_frame(tmp_path / "frame"))]}


@pytest.mark.parametrize("host", ["viewer", "replay"])
def test_a_host_loads_the_renderer_where_it_is_and_writes_no_dll(stand_ins, tmp_path, host):
    renderer, settings = stand_ins["trainer"], tmp_path / "renderer settings"
    settings.mkdir()
    before = sorted(path.name for path in renderer.parent.iterdir())
    result, log = _run(_commands(stand_ins, renderer, settings, tmp_path)[host], tmp_path)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    # The module the host loaded is the bundle's file itself, not a copy of it.
    assert log[0].startswith("module ") and Path(log[0][len("module "):]).samefile(renderer), log
    # The renderer was told its settings folder before its first lookup, and only once.
    assert f"folder {settings.as_posix()}" in log and "late 0" in log, log
    assert log.index("call RomOpen") > 0 and "call InitiateGFX" in log, log
    # Nothing new beside the renderer, nothing in the settings or temp folders, no DLL anywhere.
    assert sorted(path.name for path in renderer.parent.iterdir()) == before
    assert not any(settings.iterdir()) and not any((tmp_path / "temp").iterdir())
    assert [path for path in tmp_path.rglob("*") if path.suffix.lower() == ".dll"] == []
    if host == "replay":
        assert (tmp_path / "out" / "0000.raw").stat().st_size == 4 * 2 * 3


@pytest.mark.parametrize("host", ["viewer", "replay"])
def test_a_renderer_that_cannot_run_alone_never_draws(stand_ins, tmp_path, host):
    """LINK's own renderer would read and write the folder it sits in (Project64's Plugin folder),
    and an older Trainer build cannot run its renderer alone: the host unloads such a DLL before
    any plugin call and says why."""
    settings = tmp_path / "settings"
    settings.mkdir()
    result, log = _run(_commands(stand_ins, stand_ins["stock"], settings, tmp_path)[host], tmp_path)
    assert result.returncode != 0
    assert b"SM64RendererOnlyV1" in result.stderr
    assert [line for line in log if line.startswith("call ")] == [], log


@pytest.mark.parametrize("named", ["relative", "missing"])
def test_the_renderer_refuses_a_folder_that_is_not_an_existing_absolute_one(stand_ins, tmp_path, named):
    folder = "settings" if named == "relative" else str(tmp_path / "not here")
    result, log = _run(_commands(stand_ins, stand_ins["trainer"], Path(folder), tmp_path)["viewer"], tmp_path)
    assert result.returncode != 0 and b"refused the settings folder" in result.stderr
    assert [line for line in log if line.startswith("call ")] == [], log


# ---------------------------------------------------------------- who decides what loads


@pytest.mark.parametrize("store", ["generalEmulation\\enableShadersStorage=1\n", ""], ids=["on", "absent"])
def test_the_settings_folder_is_stable_named_and_leaves_project64_alone(tmp_path, store):
    plugin, lab = tmp_path / "Project64" / "Plugin", tmp_path / "lab"
    _settings_files(plugin, store)
    original = {path.name: path.read_bytes() for path in plugin.iterdir()}
    first = replay.settings_folder(plugin, lab_root=lab)
    assert first == (lab / "renderer-settings" / "project64").resolve()
    assert replay.settings_folder(plugin, lab_root=lab) == first          # the same folder every start
    ini = (first / "GLideN64.ini").read_text(encoding="utf-8")
    assert f"textureFilter\\txPath={(first / 'textures').as_posix()}" in ini
    # No shader store in a folder every start shares: off in the profile GLideN64 reads, and per ROM.
    profile = ini.split("[User]\n", 1)[1]
    assert "generalEmulation\\enableShadersStorage=0\n" in profile and "enableShadersStorage=1" not in ini
    custom = (first / "GLideN64.custom.ini").read_text(encoding="utf-8")
    assert "generalEmulation\\enableShadersStorage=0\n" in custom
    sized = replay.settings_folder(plugin, (1600, 1200), lab_root=lab)
    assert sized == (lab / "renderer-settings" / "1600x1200").resolve()
    for name in replay.CONFIG_FILES:                                       # GLideN64.custom.ini sets it per ROM
        text = (sized / name).read_text(encoding="utf-8")
        assert "video\\windowedWidth=1600" in text and "video\\windowedHeight=1200" in text
    assert {path.name: path.read_bytes() for path in plugin.iterdir()} == original
    assert sorted(path.name for path in first.iterdir()) == sorted(replay.CONFIG_FILES)


def test_the_scene_viewer_starts_its_host_on_the_bundle_renderer(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle" / paths.WRAPPER_DLL_NAME
    bundle.parent.mkdir()
    bundle.write_bytes(b"MZ stand-in")
    monkeypatch.setattr(paths, "bundled_plugin_dll", lambda: bundle)
    started = []

    class Recorded:
        def __init__(self, renderer, settings, header, ghosts=False):
            started.append((renderer, settings, header, ghosts))

    monkeypatch.setattr(scene_session.viewer, "Host", Recorded)
    plugin, lab = tmp_path / "Plugin", tmp_path / "lab"
    _settings_files(plugin)
    config = SimpleNamespace(pj64_plugin=plugin, renderer="trainer", resolution=None, lab_root=lab)
    owned = scene_session.renderer_host(config)(HEADER)
    renderer, settings, header, ghosts = started[0]
    assert renderer == bundle.resolve() and header == HEADER and ghosts
    assert settings == (lab / "renderer-settings" / "project64").resolve()
    assert owned.keeps_rdram                                                # frame-buffer emulation off
    assert list(tmp_path.rglob("*.dll")) == [bundle]                        # nothing copied

    for other in ("link", str(tmp_path / "candidate.dll")):
        with pytest.raises(scene_session.Refused, match="only with the Trainer's own renderer"):
            scene_session.renderer_host(SimpleNamespace(pj64_plugin=plugin, renderer=other, resolution=None,
                                                        lab_root=lab))(HEADER)
    monkeypatch.setattr(paths, "bundled_plugin_dll", lambda: None)
    with pytest.raises(scene_session.Refused, match="build_natives"):
        scene_session.renderer_host(config)(HEADER)

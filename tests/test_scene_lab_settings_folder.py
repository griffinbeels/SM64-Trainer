"""The renderer settings folder always fits, and a host that ends says why (tools/scene_lab/replay.py, viewer.py).

viewer_host refuses a settings folder longer than 196 characters (renderer_load.h), exits 3 with that reason on its
stderr, and the page used to read only "Broken pipe" (rom-models, 2026-11-09). A long Windows account name makes the
lab root's folder that long. replay.fitting_folder picks a folder that fits (the lab root's, its 8.3 short name, or
a short spare under ProgramData) or refuses with one plain sentence before any host starts; viewer.Host's errors
carry the host's own reason.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import replay  # noqa: E402
import scene_session  # noqa: E402
import viewer  # noqa: E402


def _plugin(tmp_path) -> Path:
    plugin = tmp_path / "Plugin"
    plugin.mkdir()
    (plugin / "GLideN64.ini").write_text("[User]\nvideo\\windowedWidth=640\nvideo\\windowedHeight=480\n",
                                         encoding="utf-8")
    (plugin / "GLideN64.custom.ini").write_text("[SUPER MARIO 64]\n", encoding="utf-8")
    return plugin


def _long_lab_root(tmp_path) -> Path:
    """A lab root as a very long Windows account name makes it: the settings folder under it passes 196."""
    root = tmp_path / ("a-very-long-windows-account-name-" * 4) / "AppData" / "Local" / "SM64Trainer" / "scene-lab"
    assert len(str(root / replay.SETTINGS_FOLDER_NAME / "project64")) > replay.MAX_SETTINGS_CHARS
    return root


def test_a_short_lab_root_keeps_its_own_folder(tmp_path):
    folder = replay.settings_folder(_plugin(tmp_path), lab_root=tmp_path / "lab")
    assert folder == (tmp_path / "lab" / "renderer-settings" / "project64").resolve()
    assert (folder / "GLideN64.ini").is_file()


def test_a_long_profile_path_gets_a_folder_the_renderer_takes(tmp_path, monkeypatch):
    spare = tmp_path / "s"
    monkeypatch.setattr(replay, "_short_path", lambda path: path)         # a volume without 8.3 names
    monkeypatch.setattr(replay, "_spare_roots", lambda: [spare])
    folder = replay.settings_folder(_plugin(tmp_path), lab_root=_long_lab_root(tmp_path))
    assert folder == spare / "project64" and len(str(folder)) <= replay.MAX_SETTINGS_CHARS
    text = (folder / "GLideN64.ini").read_text(encoding="utf-8")
    assert "enableShadersStorage=0" in text and (folder / "GLideN64.custom.ini").is_file()


def test_the_windows_short_name_is_tried_before_a_spare_folder(tmp_path, monkeypatch):
    short = tmp_path / "SHORT~1"
    monkeypatch.setattr(replay, "_short_path", lambda path: short)
    monkeypatch.setattr(replay, "_spare_roots", lambda: pytest.fail("a spare folder was made though the short name fits"))
    assert replay.fitting_folder(_long_lab_root(tmp_path) / "renderer-settings" / "project64", "project64") == short


def test_the_real_short_name_lookup_answers_for_an_existing_folder(tmp_path):
    assert replay._short_path(tmp_path).exists()


def test_no_folder_that_fits_is_one_plain_sentence_before_any_host_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "_short_path", lambda path: path)
    monkeypatch.setattr(replay, "_spare_roots", lambda: [])
    monkeypatch.setattr(replay, "trainer_renderer", lambda: tmp_path / "sm64_trainer_gfx.dll")
    monkeypatch.setattr(viewer, "Host", lambda *args, **kwargs: pytest.fail("a host started anyway"))
    config = SimpleNamespace(pj64_plugin=_plugin(tmp_path), renderer="trainer", resolution=None,
                             lab_root=_long_lab_root(tmp_path))
    with pytest.raises(scene_session.Refused) as refused:
        scene_session.renderer_host(config)("00" * 64)
    assert str(refused.value) == replay.TOO_LONG


def test_a_host_that_ended_says_its_own_reason_not_broken_pipe(tmp_path):
    reason = "viewer_host: the renderer refused the settings folder (it must be an existing, drive-absolute folder)"
    script = f"import sys; sys.stderr.write({reason!r} + '\\n'); sys.stderr.flush(); sys.exit(3)"
    host = viewer.Host(Path("renderer.dll"), tmp_path, "00" * 64, command=[sys.executable, "-c", script])
    host.process.wait(timeout=30)
    with pytest.raises(RuntimeError) as ended:
        for _ in range(64):                            # the first writes may land in the pipe's buffer
            host._write(b"x" * 65536, b"", b"", True)
    assert "refused the settings folder" in str(ended.value) and "exited (3)" in str(ended.value)
    assert "Broken pipe" not in str(ended.value)
    try:
        host.process.stdin.close()
    except OSError:
        pass

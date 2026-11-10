import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from build_exe import app_args, bootstrap_args, needs_reexec  # noqa: E402
import build_exe  # noqa: E402
import bundle_policy  # noqa: E402
import pytest  # noqa: E402


def test_app_args_is_onedir():
    args = app_args(None)
    assert "--onefile" not in args
    assert "--windowed" in args
    assert "SM64Trainer" in args
    assert any("gui_entry.py" in a for a in args)
    assert any("rthook_comtypes" in a for a in args)   # runtime hook kept


def test_app_args_bundles_ffmpeg_when_given(tmp_path):
    ff = tmp_path / "ffmpeg.exe"
    ff.write_bytes(b"x")
    args = app_args(str(ff))
    assert "--add-binary" in args


def test_bootstrap_args_is_tiny_onefile():
    args = bootstrap_args()
    assert "--onefile" in args
    assert "SM64TrainerSetup" in args
    assert any("bootstrap_entry.py" in a for a in args)
    assert "--collect-all" not in args
    assert "--runtime-hook" not in args


def test_needs_reexec_gates_on_hash_seed():
    assert needs_reexec({}) is True
    assert needs_reexec({"PYTHONHASHSEED": "random"}) is True
    assert needs_reexec({"PYTHONHASHSEED": "1"}) is False


def test_app_args_select_individual_tracked_ui_files():
    args = app_args(None)
    sources = {args[i + 1].rsplit(build_exe.SEP, 1)[0] for i, arg in enumerate(args) if arg == "--add-data"}
    ui = build_exe.REPO / "src/sm64_events/ui"
    assert str(ui) not in sources
    assert {str(file) for file in bundle_policy.ui_inputs(build_exe.REPO)} <= sources


def test_app_args_refuse_incidental_package_data(tmp_path, monkeypatch):
    for name in bundle_policy.DATA_FILES:
        (tmp_path / name).write_bytes(b"runtime fixture")
    (tmp_path / "local-service.json").write_text("synthetic local settings")
    monkeypatch.setattr(build_exe, "DATA_DIR", tmp_path)
    with pytest.raises(ValueError, match="unexpected first-party data"):
        app_args(None)

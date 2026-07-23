import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from build_exe import app_args, bootstrap_args, needs_reexec  # noqa: E402


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

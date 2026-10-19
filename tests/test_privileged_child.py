"""Administrative helpers use the shared interpreter resolver without a venv hop."""
import sys
from pathlib import Path

from sm64_events.core.childproc import isolated_python_child


def test_source_helper_is_windowless_and_ignores_python_customization(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "_base_executable", "C:/Python/python.exe")
    monkeypatch.setattr(Path, "is_file", lambda path: path.name == "pythonw.exe")
    command = isolated_python_child(Path("helper.py"), "--admin-helper", ["123"])
    assert Path(command[0]).name == "pythonw.exe"
    assert command[1:] == ["-I", "-S", "helper.py", "123"]


def test_packaged_helper_reenters_its_own_executable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:/Trainer/SM64Trainer.exe")
    assert isolated_python_child(Path("helper.py"), "--admin-helper", ["123"]) == [
        sys.executable, "--admin-helper", "123"]

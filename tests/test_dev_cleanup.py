"""Session startup may observe servers; it must never terminate them."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def cleanup():
    path = Path(__file__).resolve().parents[1] / "tools" / "dev_cleanup.py"
    spec = importlib.util.spec_from_file_location("dev_cleanup_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("arguments", [[], ["--report"]])
def test_session_start_preserves_launchers_and_nonlistening_servers(
        cleanup, monkeypatch, capsys, arguments):
    # Observed failure: the venv launcher has no socket; its interpreter child
    # owns the listener. Booting/viewer-only servers can also have no listener.
    processes = [
        {"pid": 3548, "cmd": '".venv/Scripts/python.exe" -m sm64_events.main'},
        {"pid": 86208, "cmd": '"uv/python.exe" -m sm64_events.main'},
        {"pid": 100003, "cmd": 'python -m sm64_events.main'},
        {"pid": 100004, "cmd": 'python -m http.server 9876'},
    ]
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {"procs": processes, "listening": [86208]}))

    monkeypatch.setattr(cleanup.subprocess, "run", run)
    monkeypatch.setattr(cleanup.sys, "argv", ["dev_cleanup.py", *arguments])
    assert cleanup.main() == 0
    assert calls == [["powershell.exe", "-NoProfile", "-Command", cleanup._PS_ENUM]]
    output = capsys.readouterr().out
    assert "3548" in output and "86208" in output
    assert "killed" not in output and "would kill" not in output


def test_unavailable_process_inventory_is_nonfatal(cleanup, monkeypatch, capsys):
    def unavailable():
        raise PermissionError("inventory denied")

    monkeypatch.setattr(cleanup, "_enumerate", unavailable)
    monkeypatch.setattr(cleanup.sys, "argv", ["dev_cleanup.py"])
    assert cleanup.main() == 0
    assert "skipped" in capsys.readouterr().err

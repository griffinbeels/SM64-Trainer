# tests/test_relaunch.py
"""Full-process relaunch primitives for the one-click restart."""
import subprocess
import sys

from sm64_events.core import relaunch


def test_wait_port_free_true_when_alive_goes_false():
    seq = iter([True, True, False])
    assert relaunch.wait_port_free(
        timeout_s=1.0, poll_s=0.01, alive=lambda: next(seq, False)) is True


def test_wait_port_free_times_out_when_always_alive():
    assert relaunch.wait_port_free(
        timeout_s=0.05, poll_s=0.01, alive=lambda: True) is False


def test_spawn_replacement_relaunches_orig_argv(monkeypatch):
    captured = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: captured.update(argv=argv,
                                                           env=kw.get("env")))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "orig_argv",
                        [sys.executable, "-m", "sm64_events.desktop"])
    relaunch.spawn_replacement()
    assert captured["argv"] == [sys.executable, "-m", "sm64_events.desktop"]
    assert captured["env"]["SM64_RESTART"] == "1"


def test_spawn_replacement_frozen_uses_executable(monkeypatch):
    captured = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: captured.update(argv=argv))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\app\sm64_tracker.exe")
    monkeypatch.setattr(sys, "orig_argv", [r"C:\app\sm64_tracker.exe", "--x"])
    relaunch.spawn_replacement()
    assert captured["argv"] == [r"C:\app\sm64_tracker.exe", "--x"]


def test_spawn_replacement_scrubs_pyinstaller_env(monkeypatch):
    # The onefile self-relaunch must drop the parent's PyInstaller bootloader
    # vars so the child extracts its own bundle (else: missing _cffi_backend).
    captured = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: captured.update(env=kw.get("env")))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\app\sm64_tracker.exe")
    monkeypatch.setattr(sys, "orig_argv", [r"C:\app\sm64_tracker.exe"])
    monkeypatch.setenv("_MEIPASS2", r"C:\Temp\_MEI42")
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI42")
    monkeypatch.setenv("KEEP_ME", "yes")
    relaunch.spawn_replacement()
    env = captured["env"]
    assert "_MEIPASS2" not in env
    assert "_PYI_APPLICATION_HOME_DIR" not in env
    assert env["SM64_RESTART"] == "1"
    assert env["KEEP_ME"] == "yes"   # unrelated vars are preserved

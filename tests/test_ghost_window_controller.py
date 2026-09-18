"""The trainer's side of the overlay: start it, talk to it, leave nothing.

Every case here runs a STAND-IN child (a few lines of Python that record what
they were told), never the real window host, so the suite can prove the
lifecycle without anything reaching the screen. Griffin has found orphaned
processes before we did; `test_stop_leaves_nothing_running` is the case that
exists because of it.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.window import controller as controller_module
from sm64_events.ghost.window.controller import (
    GHOST_WINDOW_FLAG,
    GhostWindowController,
    ghost_window_command,
)

GHOST_URL = "http://127.0.0.1:8065/ui/ghost/index.html?kind=star&entity_key=bob-1"
OTHER_URL = "http://127.0.0.1:8065/ui/ghost/index.html?kind=segment&entity_key=s1"

RECORDING_CHILD = """\
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
out.write_text("", encoding="utf-8")
print(json.dumps({"event": "ready"}), flush=True)
with out.open("a", encoding="utf-8") as fh:
    for line in sys.stdin:
        fh.write(line)
        fh.flush()
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("quit"):
            break
"""

STUBBORN_CHILD = """\
import time
print('{"event": "ready"}', flush=True)
while True:
    time.sleep(0.2)
"""


def wait_until(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def child_script(tmp_path):
    def make(source, name):
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        return path
    return make


@pytest.fixture
def recording(tmp_path, child_script):
    log = tmp_path / "control.jsonl"
    script = child_script(RECORDING_CHILD, "recording_child.py")
    return log, [sys.executable, str(script), str(log)]


def lines_in(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(raw) for raw in log.read_text(encoding="utf-8").splitlines() if raw.strip()]


def test_the_command_starts_the_overlay_module_with_its_settings():
    command = ghost_window_command(url=GHOST_URL, opacity=0.35, visible=True,
                                   backend="webview", title="Project64")
    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "sm64_events.ghost.window"]
    assert "--url" in command and GHOST_URL in command
    assert "--visible" in command
    assert command[command.index("--opacity") + 1] == "0.35"
    assert command[command.index("--backend") + 1] == "webview"


def test_the_frozen_build_needs_its_own_entry_flag(monkeypatch):
    """A PyInstaller build has no `-m`: sys.executable is the app itself, so
    the shipped exe re-enters through this flag. The flag is named here so the
    entry point and the controller cannot drift apart."""
    monkeypatch.setattr(controller_module.sys, "frozen", True, raising=False)
    command = ghost_window_command(url=GHOST_URL, opacity=0.5, visible=False,
                                   backend="webview", title="Project64")
    assert command[0] == sys.executable
    assert command[1] == GHOST_WINDOW_FLAG
    assert "-m" not in command


def test_starting_the_overlay_goes_through_the_one_quiet_spawn_door(monkeypatch, recording):
    """CREATE_NO_WINDOW and STARTF_FORCEOFFFEEDBACK: without them the overlay
    opens a Terminal window and flashes the pointer's busy spinner, mid-play."""
    seen = {}
    real_popen = controller_module.subprocess.Popen

    def spy(command, **kwargs):
        seen.update(kwargs)
        return real_popen(command, **kwargs)

    monkeypatch.setattr(controller_module.subprocess, "Popen", spy)
    _log, command = recording
    with GhostWindowController(url=GHOST_URL, command=command) as ghost:
        assert ghost.is_running()
    expected = quiet_spawn_kwargs()
    assert seen["creationflags"] == expected["creationflags"]
    assert seen["startupinfo"] is not None


def test_every_setting_reaches_the_child_as_one_json_line(recording):
    log, command = recording
    with GhostWindowController(url=GHOST_URL, command=command) as ghost:
        ghost.set_opacity(0.25)
        ghost.set_visible(True)
        ghost.set_url(OTHER_URL)
        assert wait_until(lambda: len(lines_in(log)) >= 3)
        sent = lines_in(log)
    assert sent[0] == {"opacity": 0.25}
    assert sent[1] == {"visible": True}
    assert sent[2] == {"url": OTHER_URL}


def test_the_controller_remembers_what_it_was_told(recording):
    _log, command = recording
    ghost = GhostWindowController(url=GHOST_URL, command=command)
    ghost.set_opacity(0.8)
    ghost.set_visible(True)
    assert ghost.opacity == pytest.approx(0.8)
    assert ghost.visible is True
    assert not ghost.is_running(), "setting a value must not start the overlay"


def test_an_out_of_range_opacity_is_refused_at_the_door(recording):
    _log, command = recording
    ghost = GhostWindowController(url=GHOST_URL, command=command)
    with pytest.raises(ValueError):
        ghost.set_opacity(1.7)
    with pytest.raises(ValueError):
        ghost.set_url("javascript:alert(1)")


def test_stop_leaves_nothing_running(recording):
    _log, command = recording
    ghost = GhostWindowController(url=GHOST_URL, command=command)
    ghost.start()
    proc = ghost._process
    assert proc.poll() is None
    ghost.stop()
    assert ghost.is_running() is False
    assert proc.poll() is not None, "the overlay process outlived its controller"


def test_a_child_that_ignores_quit_is_ended_anyway(tmp_path, child_script):
    """The quit line is the polite path. A wedged WebView2 child must still
    not survive the trainer closing the ghost."""
    script = child_script(STUBBORN_CHILD, "stubborn_child.py")
    ghost = GhostWindowController(url=GHOST_URL,
                                  command=[sys.executable, str(script)],
                                  stop_timeout_s=0.5)
    ghost.start()
    proc = ghost._process
    ghost.stop()
    assert proc.poll() is not None
    assert ghost.is_running() is False


def test_stopping_twice_and_starting_twice_are_both_safe(recording):
    _log, command = recording
    ghost = GhostWindowController(url=GHOST_URL, command=command)
    ghost.start()
    first = ghost._process
    ghost.start()
    assert ghost._process is first, "a second start must not spawn a second window"
    ghost.stop()
    ghost.stop()
    assert first.poll() is not None


def test_the_controller_forwards_what_the_child_says(recording):
    _log, command = recording
    heard = []
    with GhostWindowController(url=GHOST_URL, command=command,
                               on_event=heard.append):
        assert wait_until(lambda: any(e.get("event") == "ready" for e in heard))


def test_a_child_that_dies_on_its_own_is_reported_as_stopped(tmp_path, child_script):
    script = child_script("import sys\nsys.exit(3)\n", "quitter.py")
    ghost = GhostWindowController(url=GHOST_URL,
                                  command=[sys.executable, str(script)])
    ghost.start()
    assert wait_until(lambda: not ghost.is_running())
    ghost.set_opacity(0.3)  # must not raise once the child is gone
    ghost.stop()


def test_a_command_that_cannot_start_is_not_a_crash(tmp_path):
    ghost = GhostWindowController(
        url=GHOST_URL, command=[str(tmp_path / "there-is-no-such-exe.exe")])
    ghost.start()
    assert ghost.is_running() is False
    ghost.stop()


def test_the_controller_never_inherits_the_parents_console(recording):
    """subprocess kwargs are the contract; this pins the stream wiring so a
    later edit cannot make the child write into the trainer's own stdout."""
    _log, command = recording
    with GhostWindowController(url=GHOST_URL, command=command) as ghost:
        assert ghost._process.stdin is not None
        assert ghost._process.stdout is not None
        assert ghost._process.stdout is not sys.stdout


def test_the_real_module_is_what_the_default_command_names():
    """If the module ever moves, this fails before a player sees nothing."""
    command = ghost_window_command(url=GHOST_URL, opacity=0.5, visible=False,
                                   backend="none", title="Project64")
    probe = subprocess.run([*command[:3], "--help"], capture_output=True,
                           text=True, check=False, **quiet_spawn_kwargs())
    assert probe.returncode == 0, probe.stderr
    assert "--fixed-rect" in probe.stdout

# tests/test_single_instance.py
"""Single-instance detection + takeover. The graceful/force logic is pure
and injectable; the HTTP probe / native dialog are exercised live."""
import os
import socket

from sm64_events.desktop import single_instance as si


def test_instance_running_uses_injected_probe():
    assert si.instance_running(probe=lambda: True) is True
    assert si.instance_running(probe=lambda: False) is False


def test_take_over_graceful_when_port_frees():
    calls = {"shutdown": 0, "force": 0}
    freed = {"v": False}

    def shutdown():
        calls["shutdown"] += 1
        freed["v"] = True

    ok = si.take_over(
        shutdown=shutdown, port_free=lambda: freed["v"],
        force_kill=lambda: calls.__setitem__("force", calls["force"] + 1),
        timeout_s=1.0, poll_s=0.01)
    assert ok is True
    assert calls == {"shutdown": 1, "force": 0}


def test_take_over_force_kills_on_timeout():
    state = {"free": False, "force": 0}

    def force():
        state["force"] += 1
        state["free"] = True

    ok = si.take_over(shutdown=lambda: None, port_free=lambda: state["free"],
                      force_kill=force, timeout_s=0.05, poll_s=0.01)
    assert ok is True
    assert state["force"] == 1


def test_pid_on_port_finds_the_listening_process():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        assert si._pid_on_port(port) == os.getpid()
    finally:
        s.close()
    assert si._pid_on_port(port) is None   # closed -> no owner


def test_force_kill_port_owner_kills_found_pid(monkeypatch):
    monkeypatch.setattr(si, "_pid_on_port", lambda port=si.PORT: 4242)
    killed = []
    monkeypatch.setattr(si.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    si._force_kill_port_owner()
    assert killed == [(4242, si.signal.SIGTERM)]


def test_force_kill_port_owner_falls_back_to_pidfile(monkeypatch):
    monkeypatch.setattr(si, "_pid_on_port", lambda port=si.PORT: None)
    called = []
    monkeypatch.setattr(si, "_force_kill_pidfile", lambda: called.append(True))
    si._force_kill_port_owner()
    assert called == [True]

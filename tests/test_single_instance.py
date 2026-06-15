# tests/test_single_instance.py
"""Single-instance detection + takeover. The graceful/force logic is pure
and injectable; the HTTP probe / TCP-table lookup / native dialog are
exercised live."""
from sm64_events.desktop import single_instance as si


def test_instance_running_uses_injected_probe():
    assert si.instance_running(probe=lambda: True) is True
    assert si.instance_running(probe=lambda: False) is False


def test_take_over_graceful_when_accepted_and_port_frees():
    calls = {"shutdown": 0, "force": 0}
    freed = {"v": False}

    def shutdown():
        calls["shutdown"] += 1
        freed["v"] = True
        return True   # server ACCEPTED the graceful shutdown (2xx)

    ok = si.take_over(
        shutdown=shutdown, port_free=lambda: freed["v"],
        force_kill=lambda: calls.__setitem__("force", calls["force"] + 1),
        graceful_wait_s=1.0, poll_s=0.01)
    assert ok is True
    assert calls == {"shutdown": 1, "force": 0}   # graceful path, no force


def test_take_over_skips_graceful_wait_when_not_accepted():
    # A 404'd shutdown (returns False) must NOT wait out graceful_wait_s — it
    # goes straight to force-close. We prove it by making graceful_wait_s huge
    # and force_kill free the port instantly; a fast return means no wait.
    state = {"free": False, "force": 0}

    def force():
        state["force"] += 1
        state["free"] = True

    ok = si.take_over(shutdown=lambda: False, port_free=lambda: state["free"],
                      force_kill=force, graceful_wait_s=999.0,
                      force_wait_s=1.0, poll_s=0.01)
    assert ok is True
    assert state["force"] == 1


def test_take_over_retries_force_kill_once():
    # force_kill frees the port only on the SECOND call -> take_over retries.
    state = {"force": 0}

    def force():
        state["force"] += 1

    free = lambda: state["force"] >= 2
    ok = si.take_over(shutdown=lambda: False, port_free=free,
                      force_kill=force, force_wait_s=0.05, poll_s=0.01)
    assert ok is True
    assert state["force"] == 2


def test_force_kill_port_owner_terminates_the_listener(monkeypatch):
    monkeypatch.setattr(si, "listener_pid", lambda port=si.PORT: 4242)
    killed = []
    monkeypatch.setattr(si, "_terminate", lambda pid: killed.append(pid) or True)
    si._force_kill_port_owner()
    assert killed == [4242]


def test_force_kill_port_owner_falls_back_to_pidfile(monkeypatch):
    monkeypatch.setattr(si, "listener_pid", lambda port=si.PORT: None)
    called = []
    monkeypatch.setattr(si, "_force_kill_pidfile", lambda: called.append(True))
    si._force_kill_port_owner()
    assert called == [True]


def test_force_kill_port_owner_pidfile_when_terminate_fails(monkeypatch):
    monkeypatch.setattr(si, "listener_pid", lambda port=si.PORT: 99)
    monkeypatch.setattr(si, "_terminate", lambda pid: False)  # kill failed
    called = []
    monkeypatch.setattr(si, "_force_kill_pidfile", lambda: called.append(True))
    si._force_kill_port_owner()
    assert called == [True]

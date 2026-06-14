# tests/test_single_instance.py
"""Single-instance detection + takeover. The graceful/force logic is pure
and injectable; the HTTP probe / native dialog are exercised live."""
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

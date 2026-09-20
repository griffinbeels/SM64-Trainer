"""The UI fixture must own its port, not hope for it.

Every rendered test starts a real server through `tools/ui_fixture.py`. That
fixture used to bind port 0, read the number, CLOSE the socket and let uvicorn
bind it again later; on a 16-worker run the gap is long enough for another
worker to be handed the same freshly released port, and one of the two servers
died with `[WinError 10048] only one usage of each socket address`. Measured
2026-09-17: two browser tests failed that way in one full run, and a rerun of
the same suite was green -- which is exactly how a real defect gets filed as
flake.

The race itself cannot be reproduced on demand, so these tests pin the two
properties that remove it, and each one was proved by restoring the old shape:
the helper hands back a LIVE listener (closing it before returning turns the
first test red), and the fixture gives that listener to uvicorn instead of a
number (dropping the argument turns the third test red). Browser-free,
milliseconds.
"""
import inspect
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ui_fixture  # noqa: E402
from ui_fixture import _bound_socket  # noqa: E402


def test_the_helper_hands_back_a_live_listener_not_a_released_port():
    """Red if `_bound_socket` closes before returning, as `_free_port` did:
    a closed socket cannot even say which port it had."""
    listener = _bound_socket()
    try:
        port = listener.getsockname()[1]
        assert port > 0
        assert listener.fileno() != -1, "the socket must still be open"
        with socket.socket() as rival, pytest.raises(OSError):
            rival.bind(("127.0.0.1", port))
    finally:
        listener.close()


def test_two_fixtures_never_receive_the_same_port():
    """Holding every listener at once is what makes this true; sampling and
    releasing gives no such guarantee."""
    listeners = [_bound_socket() for _ in range(8)]
    try:
        ports = [listener.getsockname()[1] for listener in listeners]
        assert len(set(ports)) == len(ports), ports
    finally:
        for listener in listeners:
            listener.close()


def test_the_fixture_gives_that_listener_to_uvicorn():
    """A held socket uvicorn never receives is worse than the old code: the
    port would be busy when uvicorn tried to bind the number. Read from the
    real function, so dropping the argument at the call site is red."""
    source = inspect.getsource(ui_fixture.serve_ui_live)
    assert "_fixture_server_thread(server, [listener])" in source, (
        "serve_ui_live must hand its bound listener to the server thread")
    assert "_free_port" not in source, "the sample-and-release helper is gone"
    signature = inspect.signature(ui_fixture._fixture_server_thread)
    assert "sockets" in signature.parameters
    assert "server.run(sockets=sockets)" in inspect.getsource(
        ui_fixture._fixture_server_thread), "uvicorn must not bind again"

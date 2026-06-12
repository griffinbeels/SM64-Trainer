"""The proactor ConnectionResetError noise filter (server/app.py).

Browsers abort in-flight Range requests on every <video> seek; on Windows'
proactor loop asyncio's own connection_lost callback then raises
ConnectionResetError (WinError 10054). The handler must swallow exactly
that and delegate everything else to the default handler."""
from sm64_events.server.app import _quiet_connection_resets


class FakeLoop:
    def __init__(self):
        self.delegated = []

    def default_exception_handler(self, context):
        self.delegated.append(context)


def test_connection_reset_quieted_everything_else_delegates():
    loop = FakeLoop()
    _quiet_connection_resets(
        loop, {"exception": ConnectionResetError(10054, "reset"),
               "message": "connection lost"})
    assert loop.delegated == []                  # the one quieted case

    real = {"exception": RuntimeError("real bug"), "message": "x"}
    _quiet_connection_resets(loop, real)
    assert loop.delegated == [real]              # real errors pass through

    no_exc = {"message": "callback context without exception"}
    _quiet_connection_resets(loop, no_exc)
    assert loop.delegated == [real, no_exc]

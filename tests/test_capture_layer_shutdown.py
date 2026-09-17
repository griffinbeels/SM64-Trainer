"""A server-owned refresh worker and passive probe retire before shutdown ends."""
import asyncio
import threading

from sm64_events.core.capturelayer import CaptureLayer
from sm64_events.server.app import _close_capture_layer


def test_shutdown_joins_refresh_then_retries_same_probe_off_loop(tmp_path):
    entered = threading.Event()
    workers = []
    calls = []
    loop_thread = threading.get_ident()

    class Probe:
        def __call__(self):
            return None

        def close(self):
            assert threading.get_ident() != loop_thread
            assert not workers[0].is_alive()
            calls.append(self)
            if len(calls) == 1:
                raise OSError("injected release failure")

    probe = Probe()
    layer = CaptureLayer(None, None, tmp_path / "settings.json", None,
                         gpu_observation=probe, auto_refresh=True)

    def refresh(stop, **kwargs):
        workers.append(threading.current_thread())
        entered.set()
        stop.wait(5)

    layer.refresh_loop = refresh
    layer.start_refresh()
    assert entered.wait(1)
    asyncio.run(_close_capture_layer(layer))
    assert calls == [probe, probe]
    assert not workers[0].is_alive()
    layer.start_refresh()
    assert len(workers) == 1  # shutdown is terminal; an HTTP caller cannot restart it


def test_capture_cleanup_failure_does_not_skip_other_shutdown_work(caplog):
    class Stuck:
        count = 0

        def close(self):
            self.count += 1
            raise OSError("persistent injected close failure")

    owner = Stuck()
    asyncio.run(_close_capture_layer(owner))
    assert owner.count == 3
    assert "cleanup remains pending" in caplog.text

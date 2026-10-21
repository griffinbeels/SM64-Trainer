"""Offline lab dashboard only; no main.build, source lease, SDK or native handles."""
from contextlib import contextmanager
from pathlib import Path
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from find_uilab import find_uilab  # noqa: E402
if find_uilab():
    pytest.skip("uilab is required for the offline lab dashboard gate", allow_module_level=True)
from uilab.driver import get_driver  # noqa: E402
from ui_fixture import _bound_socket, _fixture_server_thread, _stop_fixture_server  # noqa: E402
from tools.m3_live_lab import LabService, mount_lab  # noqa: E402
from sm64_events.server.local_access import mount_local_access  # noqa: E402


class Recorder:
    ghost_capture = None
    pair = None
    def set_live_session(self, outgoing=None, incoming=None):
        self.pair = outgoing, incoming
    def live_session_status(self):
        return {"paused": False, "source_attached": False}


@contextmanager
def dashboard():
    recorder = Recorder()
    service = LabService(recorder, spawn_worker=False)
    app = FastAPI()
    mount_lab(app, service)
    app.mount("/ui", StaticFiles(directory=ROOT / "src/sm64_events/ui"))
    listener = _bound_socket()
    port = listener.getsockname()[1]
    mount_local_access(app, port=port, protected=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
        loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"))
    thread, errors = _fixture_server_thread(server, [listener])
    thread.start()
    try:
        end = time.monotonic() + 15
        while not server.started:
            assert thread.is_alive() and time.monotonic() < end
            time.sleep(.01)
        yield f"http://127.0.0.1:{port}/m3-lab", recorder, service
    finally:
        service.close()
        _stop_fixture_server(server, thread, errors)
        listener.close()


@pytest.mark.parametrize("width", [850, 1400])
def test_lab_controls_and_honest_metrics_render_without_source_acquisition(tmp_path, width):
    with dashboard() as (url, recorder, service), get_driver().launch(headless=True, viewport=(width, 1100)) as page:
        page.goto(url)
        page.wait_for("#metrics dd")
        assert recorder.pair is None and not service.lab_snapshot()["active"]
        page.evaluate("document.querySelector('[name=seats]').value='3';document.querySelector('[name=delay_ms]').value='120'")
        page.click("#start")
        deadline = time.monotonic() + 8
        while not service.lab_snapshot()["active"]:
            assert time.monotonic() < deadline
            page.wait_ms(20)
        assert service.lab_snapshot()["settings"]["delay_ms"] == 120 and all(recorder.pair)
        assert page.evaluate("return document.documentElement.scrollWidth <= innerWidth")
        (tmp_path / f"lab-{width}.png").write_bytes(page.screenshot())
        page.click("#stop")
        while service.lab_snapshot()["active"]:
            assert time.monotonic() < deadline
            page.wait_ms(20)
        assert recorder.pair == (None, None) and page.problems() == []

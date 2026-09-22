"""The full run's record of a failing page (tests/conftest.py).

A wait that fails inside a fixture has its page closed before the report
exists, so the wait records its own page before raising: a picture, and the
requests the page is still waiting on. A page that stays blank because the
data it renders from never arrived is then a named URL, not a dark screenshot.
"""
import http.server
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

import conftest  # noqa: E402
from uilab import driver  # noqa: E402


def _server_that_never_answers_its_data(release: threading.Event):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/stalled.json":
                release.wait(30)   # the data the page renders from never comes
                return
            page = b"<p>shell</p><script>fetch('/stalled.json')</script>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_wait_that_times_out_records_its_page_and_what_it_still_waits_for(
        tmp_path, monkeypatch, request):
    from playwright.sync_api import Browser, BrowserContext, Locator
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    for owner, name in ((Locator, "wait_for"), (Browser, "new_page"), (BrowserContext, "new_page")):
        monkeypatch.setattr(owner, name, getattr(owner, name))   # put back after this test
    monkeypatch.setenv("SM64_REPORT_DIR", str(tmp_path))
    conftest._keep_failure_screenshots()
    release = threading.Event()
    server = _server_that_never_answers_its_data(release)
    try:
        with driver.get_driver().launch(headless=True) as page:
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            with pytest.raises(PlaywrightTimeout):
                page.wait_for(".a-card-that-never-comes", timeout_ms=1000)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
    stem = f"{conftest._stem(request.node.nodeid)}-wait-timeout"
    assert sorted(path.name for path in (tmp_path / "screenshots").iterdir()) == [
        f"{stem}.png", f"{stem}.txt"]
    note = (tmp_path / "screenshots" / f"{stem}.txt").read_text(encoding="utf-8").splitlines()
    assert note[0] == "unanswered requests:"
    assert note[1].endswith("/stalled.json"), note
    assert note[-1].startswith("document.readyState: "), note

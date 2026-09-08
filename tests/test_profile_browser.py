"""Exercise the actual profiling CLI, including an independently injected stall."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from sm64_events.core.childproc import quiet_spawn_kwargs

ROOT = Path(__file__).resolve().parents[1]


def test_browser_observer_lifecycle():
    from frontend_runner import run_frontend
    run_frontend("profiling.test.js")


def test_browser_trace_detects_known_main_thread_stall(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            html = b'''<!doctype html><title>Profiler witness</title>
            <button id="stall" onclick="const end=performance.now()+120; while(performance.now()<end){}">Stall</button>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html)
        def log_message(self, *_):
            pass  # The test owns this quiet, localhost-only fixture.
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    actions = tmp_path / "actions.json"
    actions.write_text(json.dumps([{"action": "click", "value": "#stall"}]))
    output = tmp_path / "capture"
    try:
        result = subprocess.run([sys.executable, str(ROOT / "tools/profile_browser.py"),
            "--url", f"http://127.0.0.1:{server.server_port}", "--seconds", "2",
            "--actions", str(actions), "--output", str(output)],
            capture_output=True, text=True, timeout=45, **quiet_spawn_kwargs())
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    report = json.loads((output / "browser.json").read_text())
    assert report["status"] == "complete"
    assert report["page_errors"] == []
    assert report["observations"]["long_tasks"]["max_ms"] >= 100
    assert report["observations"]["raf_intervals"]["count"] > 0
    assert report["trace"]["bytes"] > 1000
    trace = json.loads((output / "chrome-trace.json").read_text())
    assert trace["traceEvents"], "Trace must contain real browser events"


def test_real_trainer_fixture_capture_and_report(tmp_path):
    from test_ui_replay_picture_steps import PROJECT
    workload = tmp_path / "workload.json"
    workload.write_text(json.dumps({"scenario": "isolated trainer fixture",
        "rom": "synthetic", "save_state": "fixture", "renderer": "none",
        "resolution": "1500x1000", "settings": {}, "ambient": "test job"}))
    output = tmp_path / "backend"
    actions = tmp_path / "actions.json"
    actions.write_text(json.dumps([{"action": "wait", "value": PROJECT.ready_selector}]))
    with PROJECT.open() as url:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        result = subprocess.run([sys.executable, str(ROOT / "tools/profile_capture.py"), "record",
            "--url", origin, "--workload", str(workload), "--variant", "fixture",
            "--seconds", "2", "--interval", "0.25", "--pid", str(os.getpid()),
            "--output", str(output)], capture_output=True, text=True, timeout=30,
            **quiet_spawn_kwargs())
        assert result.returncode == 0, result.stdout + result.stderr
        result = subprocess.run([sys.executable, str(ROOT / "tools/profile_browser.py"),
            "--url", url, "--seconds", "3", "--actions", str(actions),
            "--output", str(tmp_path / "browser"), "--screenshot"], capture_output=True, text=True,
            timeout=45, **quiet_spawn_kwargs())
        assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "browser/browser.json").read_text())
    assert report["page_errors"] == []
    assert report["observations"]["raf_intervals"]["count"] > 0
    result = subprocess.run([sys.executable, str(ROOT / "tools/profile_report.py"), str(output)],
        capture_output=True, text=True, timeout=15, **quiet_spawn_kwargs())
    summary = json.loads(result.stdout)
    # This fixture intentionally has no emulator poller or recorder. A working
    # collector must refuse to present absent stage observations as zero cost.
    assert result.returncode == 1, result.stdout + result.stderr
    assert not summary["valid"]
    assert summary["issues"] == ["No instrumented stage observations"]
    assert summary["metadata"]["complete"]
    assert summary["metrics"]["system.cpu_percent"]["count"] >= 2
    assert (tmp_path / "browser/screen.png").is_file()
    print(f"Profiler UI screenshot: {tmp_path / 'browser/screen.png'}")

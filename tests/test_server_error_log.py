"""Errors reach the app's own log file (logs/sm64_events.log), not only a console window.

His report, 2026-11-09: GET /api/ghost/storage answered 500 on his test server and the log showed nothing ("For
errors, shouldn't you be able to access the log history?"). uvicorn's default logging kept "Exception in ASGI
application" on a console handler that never reached the root's file. core/logging_setup.py::server_log_config is
the one uvicorn log config of a source server (main.run) and the installed app (desktop/server_runner.py); a Ghost
Lab child's stderr is copied into the log by core/childproc.py::log_child_stderr.
"""
import logging
import subprocess
import sys
import textwrap

from sm64_events.core.childproc import CHILD_LOG_LINES, log_child_stderr, quiet_spawn_kwargs
from sm64_events.core.logging_setup import server_log_config

SERVER = textwrap.dedent("""
    import sys, threading, time, urllib.request, urllib.error
    from pathlib import Path
    import uvicorn
    from fastapi import FastAPI
    from sm64_events.core.logging_setup import configure_logging, server_log_config

    configure_logging(Path(sys.argv[1]))
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise TypeError("'TemplateStore' object is not callable")

    @app.get("/fine")
    def fine():
        return {"ok": True}

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(sys.argv[2]),
                                           log_config=server_log_config(console=sys.argv[3] == "1")))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    for path in ("/fine", "/fine", "/boom"):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[2]}{path}", timeout=10).read()
        except urllib.error.HTTPError as error:
            print(path, error.code)
    server.should_exit = True
    thread.join(timeout=10)
""")


def _free_port() -> int:
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve(tmp_path, console: bool) -> tuple[str, str]:
    done = subprocess.run([sys.executable, "-c", SERVER, str(tmp_path), str(_free_port()), "1" if console else "0"],
                          capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
                          **quiet_spawn_kwargs())
    assert "/boom 500" in done.stdout, done.stdout + done.stderr[-2000:]
    return (tmp_path / "sm64_events.log").read_text(encoding="utf-8"), done.stderr


def test_a_routes_unhandled_error_lands_in_the_log_file_with_its_traceback(tmp_path):
    log, _console = _serve(tmp_path, console=False)
    assert "ERROR uvicorn.error Exception in ASGI application" in log
    assert "Traceback (most recent call last)" in log
    assert "TypeError: 'TemplateStore' object is not callable" in log
    assert '"GET /fine' not in log and "/fine" not in log            # no line per request in the file


def test_a_source_server_keeps_its_access_lines_on_the_console_only(tmp_path):
    log, console = _serve(tmp_path, console=True)
    assert "TypeError: 'TemplateStore' object is not callable" in log
    assert '"GET /fine HTTP/1.1" 200' in console and "/fine" not in log


def test_the_config_never_gives_uvicorns_error_logger_a_handler_of_its_own():
    for console in (True, False):
        loggers = server_log_config(console=console)["loggers"]
        assert loggers["uvicorn.error"] == {"handlers": [], "level": "INFO", "propagate": True}
        assert loggers["uvicorn"]["propagate"] is True and loggers["uvicorn.access"]["propagate"] is False


class _Kept(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.name, record.levelname, record.getMessage()))


def test_a_childs_error_output_is_logged_under_its_name_and_capped(tmp_path):
    kept, logger = _Kept(), logging.getLogger("sm64.child.cutter")
    logger.addHandler(kept)
    try:
        script = ("import sys\n"
                  "print('Traceback (most recent call last):', file=sys.stderr)\n"
                  f"[print('line', n, file=sys.stderr) for n in range({CHILD_LOG_LINES + 50})]\n")
        child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 **quiet_spawn_kwargs())
        thread = log_child_stderr(child, "cutter")
        child.wait(timeout=30)
        thread.join(timeout=10)
    finally:
        logger.removeHandler(kept)
    assert kept.lines[0] == ("sm64.child.cutter", "WARNING", "Traceback (most recent call last):")
    assert len(kept.lines) == CHILD_LOG_LINES + 1 and "not logged" in kept.lines[-1][2]


def test_every_lab_child_keeps_its_stderr_for_the_log():
    """No Ghost Lab child throws its error output away: each spawn site pipes it to log_child_stderr."""
    from pathlib import Path
    lab = Path(__file__).resolve().parents[1] / "tools" / "scene_lab"
    for name in ("attempt_cutter", "state_resolver", "picture_encoder", "stems_live", "evaluator_child", "rig_path",
                 "ghost_sound"):
        source = (lab / f"{name}.py").read_text(encoding="utf-8")
        assert "stderr=subprocess.DEVNULL" not in source, name
        assert source.count("log_child_stderr(") >= 1, name


def test_both_servers_use_the_one_log_config():
    """A source server (main.run) and the installed app (desktop/server_runner.py) hand uvicorn server_log_config."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "sm64_events"
    assert "log_config=server_log_config(console=True)" in (src / "main.py").read_text(encoding="utf-8")
    assert "log_config=server_log_config(" in (src / "desktop" / "server_runner.py").read_text(encoding="utf-8")

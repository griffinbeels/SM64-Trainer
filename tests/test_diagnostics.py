# tests/test_diagnostics.py
"""The debug report must build WHEN things are broken — that is its purpose.
Every section is independently wrapped; a dead source names itself."""
from collections import namedtuple
from pathlib import Path
import os
import subprocess

import pytest

from sm64_events.core import diagnostics

Row = namedtuple("Row", "id session_id seq type frame wall_time_utc payload")


def _healthy_kwargs(tmp_path: Path) -> dict:
    log = tmp_path / "sm64_events.log"
    log.write_text("\n".join(
        [f"2026-08-06T12:00:{i % 60:02d}Z INFO sm64.replay line {i}"
         for i in range(500)]
        + ["2026-08-06T12:09:00Z ERROR sm64.poller read failed",
           "2026-08-06T12:09:01Z WARNING sm64.replay ring low"]),
        encoding="utf-8")
    uilog = tmp_path / "ui_log.jsonl"
    uilog.write_text('{"surface": "selector"}\n' * 60, encoding="utf-8")
    perf = tmp_path / "perf_log.jsonl"
    perf.write_text('{"rss": 1}\n' * 40, encoding="utf-8")
    return dict(
        version="1.7.1", frozen=False, port=8065, data_dir=str(tmp_path),
        log_file=log, ui_log_file=uilog, perf_log_file=perf,
        health=lambda: {"status": "ok", "emulator_attached": False},
        events=lambda: [Row(1, 1, 0, "star_collected", 1350,
                            "2026-08-06T12:00:00Z", {"course_id": 2})])


def test_healthy_report_has_every_section_and_caps(tmp_path):
    report = diagnostics.build_report(**_healthy_kwargs(tmp_path))
    for heading in ("# SM64 Trainer debug report", "## Health", "## Problems",
                    "## Server log", "## Recent game events",
                    "## What the screen drew", "## Perf samples"):
        assert heading in report
    assert "star_collected" in report
    assert "ERROR" in report and "read failed" in report
    # cap: 502 log lines written, only the newest LOG_TAIL_LINES included
    assert "line 0\n" not in report
    assert f"line {500 - diagnostics.LOG_TAIL_LINES + 2}" in report
    # ui log capped to UILOG_TAIL of the 60 written
    assert report.count('{"surface": "selector"}') == diagnostics.UILOG_TAIL


def test_successful_requests_cannot_push_operational_lines_out_of_the_tail(tmp_path):
    # A beta.30 tester's 400-line tail held 194 access-200 lines from the page's polling.
    lines = []
    for index in range(300):
        lines.append('2026-10-24T22:36:44Z INFO uvicorn.access 127.0.0.1:50385 - "GET /health HTTP/1.1" 200')
        if index % 2 == 0:
            lines.append(f"2026-10-24T22:36:45Z INFO sm64.replay stage timing step {index // 2}")
    lines += ['2026-10-24T22:36:46Z INFO uvicorn.access 127.0.0.1:50385 - "GET /ui/app.js HTTP/1.1" 304',
              '2026-10-24T22:36:47Z INFO uvicorn.access 127.0.0.1:50385 - "POST /api/target HTTP/1.1" 409',
              '2026-10-24T22:36:48Z INFO uvicorn.access 127.0.0.1:50385 - "GET /api/session HTTP/1.1" 500']
    kwargs = _healthy_kwargs(tmp_path)
    kwargs["log_file"].write_text("\n".join(lines), encoding="utf-8")
    server_log = diagnostics.build_report(**kwargs).split("## Server log", 1)[1].split("## Recent game events", 1)[0]
    assert all(f"stage timing step {step}\n" in server_log for step in range(150))
    assert server_log.count("uvicorn.access") == 2
    assert '" 409' in server_log and '" 500' in server_log
    assert '" 200' not in server_log and '" 304' not in server_log


def test_the_ghost_timeline_survives_hours_of_capture_lines(tmp_path):
    # An AMD tester's report (2026-10-25) was made hours after the ghost failed;
    # its 400-line tail held nine minutes of capture lines and no ghost decision.
    kwargs = _healthy_kwargs(tmp_path)
    log = kwargs["log_file"]
    log.with_name(log.name + ".1").write_text(
        "2026-10-25T12:58:31Z INFO sm64.ghost.state ghost: target=star:14-3 strategy=False on=True\n", encoding="utf-8")
    log.write_text("\n".join(
        ["2026-10-25T13:06:47Z ERROR sm64.ghost.state A saved PB kept no Mario poses: This recording has no native Mario poses.",
         "2026-10-25T13:06:47Z INFO sm64.ghost rival C:\\Users\\Someone\\ghosts kept"]
        + [f"2026-10-25T13:07:{i % 60:02d}Z INFO sm64.replay dwm capture: {i} grabs/s" for i in range(900)]), encoding="utf-8")
    report = diagnostics.build_report(**kwargs)
    timeline = report.split("## Ghost timeline", 1)[1].split("## Server log", 1)[0]
    assert timeline.splitlines()[3:5] == [
        "2026-10-25T12:58:31Z INFO sm64.ghost.state ghost: target=star:14-3 strategy=False on=True",
        "2026-10-25T13:06:47Z ERROR sm64.ghost.state A saved PB kept no Mario poses: This recording has no native Mario poses."]
    assert "sm64.ghost rival" not in report and "Someone" not in report
    assert "kept no Mario poses" not in report.split("## Server log", 1)[1]  # beyond the tail


def test_schema_names_show_while_their_values_and_data_keys_stay_hidden(tmp_path):
    # A tester's report (2026-10-25) named almost nothing: field_0..field_23.
    kwargs = _healthy_kwargs(tmp_path)
    kwargs["events"] = lambda: [Row(1, 1, 0, "practice_reset", 578, "2026-10-25T13:09:18Z",
                                    {"mario_acted": True, "area_load": False, "strat_tag": "My Secret Strat",
                                     "classification": {"SomeRunnerName": 3}})]
    kwargs["setup"] = lambda: {"state": "needs_restart", "layer_alive": False,
                               "plugin_dir": "C:\\Users\\Someone\\Project64\\Plugin"}
    report = diagnostics.build_report(**kwargs)
    journal = report.split("## Recent game events", 1)[1]
    assert '"mario_acted": true' in journal and '"area_load": false' in journal
    assert '"strat_tag": "[text omitted]"' in journal and "My Secret Strat" not in report
    assert '"classification": {"field_0": 3}' in journal and "SomeRunnerName" not in report
    setup = report.split("## Last observed setup", 1)[1].split("## Trainer diagnostic snapshot", 1)[0]
    assert '"layer_alive": false' in setup and '"plugin_dir": "[text omitted]"' in setup
    assert "Someone" not in report


def test_every_broken_source_names_itself_instead_of_raising(tmp_path):
    kwargs = _healthy_kwargs(tmp_path)
    kwargs["log_file"] = tmp_path / "missing.log"
    kwargs["health"] = lambda: (_ for _ in ()).throw(RuntimeError("hb dead"))
    kwargs["events"] = lambda: (_ for _ in ()).throw(
        RuntimeError("journal unavailable (no db attached)"))
    report = diagnostics.build_report(**kwargs)
    assert "unavailable" in report
    assert "journal unavailable" in report
    assert "hb dead" in report
    assert "## Perf samples" in report  # healthy sections still render


def test_empty_ui_log_prints_the_established_reading(tmp_path):
    kwargs = _healthy_kwargs(tmp_path)
    kwargs["ui_log_file"] = tmp_path / "never_written.jsonl"
    report = diagnostics.build_report(**kwargs)
    assert "old JS" in report  # silence means a stale tab, not a quiet screen


def test_write_report_prunes_to_keep(tmp_path):
    for stamp in range(7):
        (tmp_path / f"debug-report-2026080{stamp}-000000Z.md").write_text("x")
    out = diagnostics.write_report(tmp_path, "# new", keep=5)
    survivors = sorted(p.name for p in tmp_path.glob("debug-report-*.md"))
    assert out.name in survivors
    assert len(survivors) == 3  # A caller cannot raise the app's retention cap.
    assert "debug-report-20260800-000000Z.md" not in survivors


def test_diagnostics_dir_sits_inside_the_reveal_root():
    from sm64_events.core.paths import diagnostics_dir, replays_root
    assert diagnostics_dir().resolve().is_relative_to(replays_root().resolve())


def test_export_refuses_to_grow_when_owned_old_reports_cannot_be_removed(tmp_path, monkeypatch):
    for index in range(3):
        (tmp_path / f"debug-report-{index}.md").write_text("old")
    def denied(*args, **kwargs):
        raise PermissionError("synthetic denied deletion")
    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(OSError, match="limit is full"):
        diagnostics.write_report(tmp_path, "new")
    assert len(list(tmp_path.glob("*.md"))) == 3


@pytest.mark.skipif(os.name != "nt", reason="real Windows junction boundary")
def test_export_never_deletes_through_a_junction_ancestor(tmp_path):
    from sm64_events.core.childproc import quiet_spawn_kwargs
    target, junction = tmp_path / "target", tmp_path / "redirect"
    report_dir = target / "reports"
    report_dir.mkdir(parents=True)
    old = report_dir / "debug-report-owned-looking.md"
    old.write_text("keep exactly")
    result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, text=True, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(OSError, match="redirected"):
            diagnostics.write_report(junction / "reports", "new")
        assert old.read_text() == "keep exactly" and len(list(report_dir.iterdir())) == 1
    finally:
        junction.rmdir()

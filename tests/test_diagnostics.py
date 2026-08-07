# tests/test_diagnostics.py
"""The debug report must build WHEN things are broken — that is its purpose.
Every section is independently wrapped; a dead source names itself."""
from collections import namedtuple
from pathlib import Path

from sm64_events.core import diagnostics

Row = namedtuple("Row", "id session_id seq type frame wall_time_utc payload")


def _healthy_kwargs(tmp_path: Path) -> dict:
    log = tmp_path / "sm64_events.log"
    log.write_text("\n".join(
        [f"2026-08-06T12:00:{i % 60:02d}Z INFO sm64.test line {i}"
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
    assert len(survivors) == 5
    assert "debug-report-20260800-000000Z.md" not in survivors


def test_diagnostics_dir_sits_inside_the_reveal_root():
    from sm64_events.core.paths import diagnostics_dir, replays_root
    assert diagnostics_dir().resolve().is_relative_to(replays_root().resolve())

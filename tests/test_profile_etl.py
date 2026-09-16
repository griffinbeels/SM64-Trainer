"""Offline export must surface incomplete evidence and never change a trace."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import profile_etl  # noqa: E402


def test_loss_unknown_is_not_reported_as_zero():
    assert profile_etl.loss_evidence("unrecognized trace header")["state"] == "unknown"
    assert profile_etl.loss_evidence("Events Lost: 0\nBuffers Lost: 0")["state"] == "reported_zero"
    assert profile_etl.loss_evidence("Events Lost: 1,234")["state"] == "reported_loss"


def fake_export(monkeypatch, *, failure=False):
    calls = []
    monkeypatch.setattr(profile_etl, "find_xperf", lambda: "xperf.exe")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if not failure:
            Path(command[command.index("-o") + 1]).write_text("Events Lost: 0\nreport rows", encoding="utf-8")
        return SimpleNamespace(returncode=1 if failure else 0)

    monkeypatch.setattr(profile_etl.subprocess, "run", run)
    return calls


def test_only_bounded_offline_summary_actions_and_trace_unchanged(tmp_path, monkeypatch):
    trace = tmp_path / "recording.etl"
    original = b"trace fixture"
    trace.write_bytes(original)
    calls = fake_export(monkeypatch)
    output = tmp_path / "report"
    manifest = profile_etl.export(trace, output, timeout=3)
    assert manifest["complete"]
    assert len(calls) == 6
    assert trace.read_bytes() == original
    assert manifest["symbols"] == {"requested": False, "resolved": None,
                                   "note": "Module attribution by default. Requested symbols may remain unresolved; inspect action logs and profile report."}
    for command, options in calls:
        assert command[1:3] == ["-i", str(trace)]
        assert "-a" in command and "-tle" not in command
        assert not any(arg in command for arg in ("-start", "-stop", "-d", "dumper", "-symbols"))
        assert options["timeout"] == 3
    assert json.loads((output / "manifest.json").read_text())["input"]["sha256"]


def test_invalid_trace_stops_after_header_failure(tmp_path, monkeypatch):
    trace = tmp_path / "corrupt.etl"
    trace.write_bytes(b"not an ETL")
    calls = fake_export(monkeypatch, failure=True)
    manifest = profile_etl.export(trace, tmp_path / "report")
    assert not manifest["complete"]
    assert len(calls) == 1
    assert manifest["actions"][0]["returncode"] == 1
    assert manifest["loss"]["state"] == "unknown"


def test_action_timeout_is_retained_in_manifest(tmp_path, monkeypatch):
    trace = tmp_path / "recording.etl"
    trace.write_bytes(b"trace")
    fake_export(monkeypatch)

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(profile_etl.subprocess, "run", timeout)
    manifest = profile_etl.export(trace, tmp_path / "report", timeout=1)
    assert not manifest["complete"]
    assert "timed out" in manifest["actions"][0]["error"]


def test_missing_empty_and_overwrite_refused(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):
        profile_etl.export(tmp_path / "missing.etl", tmp_path / "out")
    trace = tmp_path / "empty.etl"
    trace.touch()
    with pytest.raises(ValueError, match="nonempty"):
        profile_etl.export(trace, tmp_path / "out")
    trace.write_bytes(b"trace")
    fake_export(monkeypatch)
    with pytest.raises(FileExistsError):
        profile_etl.export(trace, tmp_path)


def test_header_only_trace_is_not_complete_despite_zero_exit_and_zero_loss(tmp_path, monkeypatch):
    trace = tmp_path / "header-only.etl"
    trace.write_bytes(b"header")
    fake_export(monkeypatch)

    def run(command, **kwargs):
        action = command[command.index("-a") + 1]
        text = ("There is no sampled profile data in the trace" if action == "profile"
                else "Events Lost: 0\nBuffers Lost: 0")
        Path(command[command.index("-o") + 1]).write_text(text)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(profile_etl.subprocess, "run", run)
    result = profile_etl.export(trace, tmp_path / "out", actions=["profile"])
    assert result["loss"]["state"] == "reported_zero"
    assert not result["complete"]
    assert "no sampled profile data" in result["actions"][1]["error"]

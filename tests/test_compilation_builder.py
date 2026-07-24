import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sm64_events.replay import compilation as comp
from sm64_events.tracking.compilation import ClipSpec


def _spec(aid, kind="failure", source="ring"):
    now = datetime.now(timezone.utc)
    return ClipSpec(attempt_id=aid, kind=kind, source=source,
                    span_start=now, span_end=now)


class FakeExtractor:
    """Writes a stub file where a real cut would go; can be told to fail one."""
    def __init__(self, fail_ids=()):
        self.calls = []
        self._fail = set(fail_ids)

    def extract(self, ring, start, end, out_path):
        self.calls.append(out_path)
        # attempt id is embedded by the builder via part_NNN — use call order
        if len(self.calls) - 1 in self._fail:
            raise ValueError("no footage overlaps the requested span")
        Path(out_path).write_bytes(b"stub")
        return None


def _builder(extractor):
    return comp.CompilationBuilder(extractor, codec="libx264", fps=60,
                                   ffmpeg="ffmpeg")


def _install(monkeypatch, fake_run):
    # Both _probe_dims and _concat_normalize call subprocess.run directly
    # (like extract.py); stub it so no real ffmpeg runs. The extractor is
    # injected separately (FakeExtractor). fake_run dispatches on whether
    # "-filter_complex" is in args (concat) vs a probe (-i, no output).
    monkeypatch.setattr(comp.subprocess, "run", fake_run)


def test_multi_clip_runs_concat_with_all_inputs(tmp_path, monkeypatch):
    calls = {}

    def fake_run(args, **kw):
        if "-filter_complex" in args:
            calls["concat"] = args
            Path(args[-1]).write_bytes(b"out")
        else:                                    # probe
            calls["probe"] = args
        return subprocess.CompletedProcess(args, 0, b"", b"Video: h264, 1280x960")

    _install(monkeypatch, fake_run)
    b = _builder(FakeExtractor())
    out = tmp_path / "c.mp4"
    res = b.build([_spec(1), _spec(2, kind="finale")], ring=None,
                  tmp_dir=tmp_path / "t", out_path=out,
                  resolve_saved=lambda i: None, progress_cb=lambda f, m: None)
    assert res.clip_count == 2 and res.skipped_runtime == 0
    assert "-filter_complex" in calls["concat"]
    assert calls["concat"].count("-i") == 2      # both clips fed
    assert "concat=n=2" in " ".join(calls["concat"])
    assert out.exists()


def test_single_clip_is_copied_not_concatenated(tmp_path, monkeypatch):
    def fake_run(args, **kw):
        raise AssertionError("ffmpeg should not run for a single clip")

    _install(monkeypatch, fake_run)
    b = _builder(FakeExtractor())
    out = tmp_path / "c.mp4"
    res = b.build([_spec(1)], ring=None, tmp_dir=tmp_path / "t", out_path=out,
                  resolve_saved=lambda i: None, progress_cb=lambda f, m: None)
    assert res.clip_count == 1
    assert out.read_bytes() == b"stub"


def test_runtime_extract_failure_is_skipped(tmp_path, monkeypatch):
    def fake_run(args, **kw):
        if "-filter_complex" in args:
            Path(args[-1]).write_bytes(b"out")
        return subprocess.CompletedProcess(args, 0, b"", b"Video: 1280x960")

    _install(monkeypatch, fake_run)
    b = _builder(FakeExtractor(fail_ids={0}))    # first spec fails to extract
    out = tmp_path / "c.mp4"
    res = b.build([_spec(1), _spec(2), _spec(3, kind="finale")], ring=None,
                  tmp_dir=tmp_path / "t", out_path=out,
                  resolve_saved=lambda i: None, progress_cb=lambda f, m: None)
    assert res.skipped_runtime == 1
    assert res.clip_count == 2


def test_saved_finale_uses_resolved_path(tmp_path, monkeypatch):
    saved = tmp_path / "pb.mp4"
    saved.write_bytes(b"pb")
    calls = {}

    def fake_run(args, **kw):
        if "-filter_complex" in args:
            calls["concat"] = args
            Path(args[-1]).write_bytes(b"out")
        return subprocess.CompletedProcess(args, 0, b"", b"Video: 1280x960")

    _install(monkeypatch, fake_run)
    b = _builder(FakeExtractor())
    out = tmp_path / "c.mp4"
    res = b.build([_spec(1), _spec(9, kind="finale", source="saved")],
                  ring=None, tmp_dir=tmp_path / "t", out_path=out,
                  resolve_saved=lambda i: saved if i == 9 else None,
                  progress_cb=lambda f, m: None)
    assert res.clip_count == 2
    assert str(saved) in calls["concat"]   # saved clip actually fed to ffmpeg


def test_finale_included_false_when_saved_finale_missing(tmp_path, monkeypatch):
    def fake_run(args, **kw):
        if "-filter_complex" in args:
            Path(args[-1]).write_bytes(b"out")
        return subprocess.CompletedProcess(args, 0, b"", b"Video: 1280x960")

    _install(monkeypatch, fake_run)
    b = _builder(FakeExtractor())
    out = tmp_path / "c.mp4"
    res = b.build([_spec(1), _spec(9, kind="finale", source="saved")],
                  ring=None, tmp_dir=tmp_path / "t", out_path=out,
                  resolve_saved=lambda i: None,      # saved finale file is gone
                  progress_cb=lambda f, m: None)
    assert res.finale_included is False
    assert res.clip_count == 1                       # only the failure survived


def test_concat_failure_unlinks_output(tmp_path, monkeypatch):
    out = tmp_path / "c.mp4"

    def fake_run(args, **kw):
        if "-filter_complex" in args:
            raise subprocess.CalledProcessError(1, args, b"", b"boom")
        return subprocess.CompletedProcess(args, 0, b"", b"Video: 1280x960")

    _install(monkeypatch, fake_run)
    b = _builder(FakeExtractor())
    with pytest.raises(RuntimeError):
        b.build([_spec(1), _spec(2)], ring=None, tmp_dir=tmp_path / "t",
                out_path=out, resolve_saved=lambda i: None,
                progress_cb=lambda f, m: None)
    assert not out.exists()


def test_empty_after_all_skipped_raises(tmp_path, monkeypatch):
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 0, b"", b"")

    _install(monkeypatch, fake_run)
    b = _builder(FakeExtractor(fail_ids={0}))
    with pytest.raises(ValueError):
        b.build([_spec(1)], ring=None, tmp_dir=tmp_path / "t",
                out_path=tmp_path / "c.mp4", resolve_saved=lambda i: None,
                progress_cb=lambda f, m: None)

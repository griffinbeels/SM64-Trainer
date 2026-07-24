from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.replay import compilation as comp
from sm64_events.tracking.compilation import (ClipSpec, CompilationPlan,
                                              EntityRef)


class FakeRing:
    def coverage(self, kind):
        return None


class FakeReplay:
    def __init__(self):
        self.recorder = SimpleNamespace(ring=FakeRing())
        self.pre_pad_s = 3.0
        self.post_pad_s = 2.0

    def _saved_attempt_ids(self):
        return set()

    def find_saved(self, attempt_id):
        return None


class FakeDB:
    def attempts(self):
        return []


class FakeTracker:
    db = FakeDB()
    segment_defs = []


class FakeBuilder:
    def __init__(self):
        self.built = None

    def build(self, specs, ring, tmp_dir, out_path, resolve_saved, progress_cb):
        progress_cb(0.5, "cutting 1/1")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x")
        self.built = specs
        return comp.CompilationResult(path=Path(out_path), clip_count=len(specs),
                                      skipped_runtime=0)


def _service(tmp_path, plan, monkeypatch):
    svc = comp.CompilationService(FakeReplay(), FakeTracker(), FakeBuilder(),
                                  out_dir=tmp_path)
    # deterministic plan (no real attempts needed); monkeypatch auto-restores
    # the real plan_compilation after the test so it can't leak across files.
    monkeypatch.setattr(comp, "plan_compilation", lambda *a, **k: plan)
    return svc


def _plan(specs, **kw):
    base = dict(failure_count=len(specs), aged_out=0, no_finale=True,
                finale_frames=None)
    base.update(kw)
    return CompilationPlan(specs=specs, **base)


def _spec(aid):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return ClipSpec(aid, "failure", "ring", now, now)


def test_start_rejects_negative_window(tmp_path, monkeypatch):
    svc = _service(tmp_path, _plan([_spec(1)]), monkeypatch)
    with pytest.raises(ValueError):
        svc.start(EntityRef(course_id=1, star_id=0), -1, 3)


def test_run_job_produces_result(tmp_path, monkeypatch):
    svc = _service(tmp_path, _plan([_spec(1)], finale_frames=600,
                                   no_finale=False), monkeypatch)
    svc._jobs["j"] = {"state": "running", "progress": 0.0,
                      "message": "planning", "result": None}
    svc._run_job("j", EntityRef(course_id=1, star_id=0), 5.0, 3.0)
    job = svc._jobs["j"]
    assert job["state"] == "done"
    assert job["result"]["clip_count"] == 1
    assert job["result"]["finale_time"] is not None
    assert Path(job["result"]["path"]).exists()


def test_run_job_errors_on_empty_plan(tmp_path, monkeypatch):
    svc = _service(tmp_path, _plan([]), monkeypatch)
    svc._jobs["j"] = {"state": "running", "progress": 0.0,
                      "message": "planning", "result": None}
    svc._run_job("j", EntityRef(course_id=1, star_id=0), 5.0, 3.0)
    assert svc._jobs["j"]["state"] == "error"


def test_status_unknown_job_raises(tmp_path, monkeypatch):
    svc = _service(tmp_path, _plan([_spec(1)]), monkeypatch)
    with pytest.raises(LookupError):
        svc.status("nope")

import asyncio
import time

import pytest

from sm64_events.compare.importer import VideoImporter
from sm64_events.compare.service import CompareService
from sm64_events.storage.db import Database


class _Bc:
    def __init__(self): self.events = []
    async def publish(self, ev): self.events.append(ev)


class _Tracker:
    def __init__(self, db, ranks): self.db = db; self.ranks = ranks


class _Ranks:
    def video_for(self, ek, strat): return None


def _svc(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()

    def runner(cmd):  # fake ffmpeg
        open(cmd[-1], "wb").write(b"norm")

    imp = VideoImporter(cache, "ffmpeg", runner=runner)
    db = Database(tmp_path / "t.db")
    return CompareService(imp, _Tracker(db, _Ranks()), _Bc(), cache), db, cache


def test_import_job_completes_and_inserts_row(tmp_path):
    svc, db, cache = _svc(tmp_path)
    src = tmp_path / "v.mp4"; src.write_bytes(b"raw")
    job = svc.start_import("star:7:0", "Ledgegrab", "mine", "file", str(src))
    for _ in range(100):                        # poll to completion
        st = svc.import_status(job)
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert st["state"] == "done", st
    assert st["comparison"]["name"] == "mine"
    assert db.comparisons("star:7:0", "Ledgegrab")[0]["name"] == "mine"


def test_import_error_reported(tmp_path):
    svc, db, cache = _svc(tmp_path)
    job = svc.start_import("star:7:0", "L", "x", "file",
                           str(tmp_path / "missing.mp4"))
    for _ in range(100):
        st = svc.import_status(job)
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert st["state"] == "error"
    assert db.comparisons("star:7:0", "L") == []  # no row on failure


def test_delete_unlinks_unreferenced_cache(tmp_path):
    svc, db, cache = _svc(tmp_path)
    src = tmp_path / "v.mp4"; src.write_bytes(b"raw")
    job = svc.start_import("star:7:0", "L", "x", "file", str(src))
    for _ in range(100):
        if svc.import_status(job)["state"] == "done":
            break
        time.sleep(0.02)
    cid = db.comparisons("star:7:0", "L")[0]["id"]
    cname = db.comparisons("star:7:0", "L")[0]["cache_name"]
    assert (cache / cname).exists()
    asyncio.run(svc.delete(cid))
    assert not (cache / cname).exists()          # last ref gone -> file removed
    assert db.comparisons("star:7:0", "L") == []


def test_unknown_job_raises(tmp_path):
    svc, _, _ = _svc(tmp_path)
    with pytest.raises(LookupError):
        svc.import_status("nope")


def test_cache_path_rejects_bad_name(tmp_path):
    svc, _, _ = _svc(tmp_path)
    with pytest.raises(LookupError):
        svc.cache_path("../secret")

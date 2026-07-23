# tests/test_update_cycle.py
"""End-to-end: fake GitHub release (real zip + manifest) -> check -> plan ->
range-fetch -> journaled apply -> installed tree matches the release.
The whole pipeline the popup's 'Update now' drives, network-free."""
import io
import threading
from pathlib import Path

from sm64_events.core.update_apply import BACKUP_DIR, read_journal
from sm64_events.core.update_plan import INSTALLED_MANIFEST
from sm64_events.core.updater import UpdateService

from test_updater import _fake_release  # reuse the fixture helpers


class _Resp(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _range_http(routes):
    """Serves routes; honors Range on any asset with 206 slices."""
    def opener(req):
        url = req.full_url if hasattr(req, "full_url") else req
        body = routes[url]
        rng = getattr(req, "headers", {}).get("Range")
        if rng:
            lo, hi = rng.removeprefix("bytes=").split("-")
            return _Resp(body[int(lo):int(hi) + 1], status=206)
        return _Resp(body)
    return opener


V1 = {"SM64Trainer.exe": b"EXE-V1", "_internal/stable.dll": b"S" * 4000,
      "_internal/old.pyd": b"OLD-ONLY"}
V2 = {"SM64Trainer.exe": b"EXE-V2", "_internal/stable.dll": b"S" * 4000,
      "_internal/fresh.dat": b"NEW-FILE"}


def _install_v1(tmp_path, routes_v1):
    """Materialize a v1 install the way the bootstrap would."""
    root = tmp_path / "app"
    for rel, content in V1.items():
        p = root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    (root / INSTALLED_MANIFEST).write_bytes(
        routes_v1["https://dl/manifest.json"])
    return root


def test_full_update_cycle_downloads_only_changes(tmp_path):
    routes_v1 = _fake_release(tmp_path / "r1", "v1.0.0", V1)
    routes_v2 = _fake_release(tmp_path / "r2", "v2.0.0", V2)
    root = _install_v1(tmp_path, routes_v1)
    svc = UpdateService(current_version="1.0.0",
                        http=_range_http(routes_v2),
                        exe_path=root / "SM64Trainer.exe",
                        state_path=tmp_path / "state.json", frozen=True)
    st = svc.status()
    assert st["update_available"] is True
    # only exe + fresh.dat move; the stable dll must NOT be re-downloaded
    zip_size = len(routes_v2["https://dl/full.zip"])
    assert 0 < st["download_bytes"] < zip_size
    done = threading.Event()
    assert svc.begin_apply(done.set)["state"] == "downloading"
    assert done.wait(timeout=10)
    assert (root / "SM64Trainer.exe").read_bytes() == b"EXE-V2"
    assert (root / "_internal/fresh.dat").read_bytes() == b"NEW-FILE"
    assert not (root / "_internal/old.pyd").exists()
    assert (root / "_internal/stable.dll").read_bytes() == b"S" * 4000
    assert read_journal(root)["state"] == "done"
    assert (root / BACKUP_DIR / "SM64Trainer.exe").read_bytes() == b"EXE-V1"
    # installed manifest advanced -> a re-check sees nothing to do
    svc2 = UpdateService(current_version="2.0.0",
                         http=_range_http(routes_v2),
                         exe_path=root / "SM64Trainer.exe",
                         state_path=tmp_path / "state.json", frozen=True)
    assert svc2.status()["update_available"] is False


def test_full_update_cycle_range_refused_falls_back(tmp_path):
    routes_v1 = _fake_release(tmp_path / "r1", "v1.0.0", V1)
    routes_v2 = _fake_release(tmp_path / "r2", "v2.0.0", V2)
    root = _install_v1(tmp_path, routes_v1)

    def no_range_http(req):        # always ignores Range -> status 200
        url = req.full_url if hasattr(req, "full_url") else req
        return _Resp(routes_v2[url])

    svc = UpdateService(current_version="1.0.0", http=no_range_http,
                        exe_path=root / "SM64Trainer.exe",
                        state_path=tmp_path / "state.json", frozen=True)
    done = threading.Event()
    svc.status()
    assert svc.begin_apply(done.set)["state"] == "downloading"
    assert done.wait(timeout=10)
    assert (root / "SM64Trainer.exe").read_bytes() == b"EXE-V2"
    assert not (root / "_internal/old.pyd").exists()

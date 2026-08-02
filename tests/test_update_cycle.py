# tests/test_update_cycle.py
"""End-to-end: fake GitHub release (real zip + manifest) -> check -> plan ->
range-fetch -> journaled apply -> installed tree matches the release.
The whole pipeline the popup's 'Update now' drives, network-free.

The second half drives the SAME pipeline through a flaky network, which is how
the one real-world failure looked (a TLS timeout mid-download, live log
2026-08-01): the worker must retry on its own and only report failure once
every attempt is spent."""
import io
import threading
import time
import urllib.error
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


# --- retrying a flaky network (the real 2026-08-01 failure) ---

ZIP_URL = "https://dl/full.zip"


def _flaky_http(routes, fails):
    """Range-serving opener whose ZIP requests raise the SAME error the live
    failure did, until `fails["left"]` is exhausted. `fails` is mutable so a
    test can end the outage mid-run."""
    inner = _range_http(routes)
    calls = {"zip": 0}

    def opener(req):
        url = req.full_url if hasattr(req, "full_url") else req
        if url == ZIP_URL:
            calls["zip"] += 1
            if fails["left"] > 0:
                fails["left"] -= 1
                raise urllib.error.URLError(
                    TimeoutError("[WinError 10060] connection attempt failed"))
        return inner(req)
    return opener, calls


def _svc_on_flaky(tmp_path, fails):
    routes_v1 = _fake_release(tmp_path / "r1", "v1.0.0", V1)
    routes_v2 = _fake_release(tmp_path / "r2", "v2.0.0", V2)
    root = _install_v1(tmp_path, routes_v1)
    http, calls = _flaky_http(routes_v2, fails)
    svc = UpdateService(current_version="1.0.0", http=http,
                        exe_path=root / "SM64Trainer.exe",
                        state_path=tmp_path / "state.json", frozen=True,
                        retry_delays=(0, 0, 0, 0))   # 5 attempts, no sleeping
    return svc, root, calls


def _wait_for_state(svc, state, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if svc.status()["state"] == state:
            return True
        time.sleep(0.01)
    return False


def test_a_transient_network_failure_is_retried_without_telling_the_user(tmp_path):
    svc, root, calls = _svc_on_flaky(tmp_path, {"left": 2})
    done = threading.Event()
    assert svc.begin_apply(done.set)["state"] == "downloading"
    assert done.wait(timeout=10)
    st = svc.status()
    assert st["attempt"] == 3                     # two outages, then through
    assert calls["zip"] > 2                       # it really re-downloaded
    assert (root / "SM64Trainer.exe").read_bytes() == b"EXE-V2"
    # The user was never shown a failure: the state only ever moved forward.
    assert st["state"] != "error"


def test_the_error_state_arrives_only_after_every_attempt_is_spent(tmp_path):
    svc, _root, calls = _svc_on_flaky(tmp_path, {"left": 99})
    svc.begin_apply(lambda: None)
    assert _wait_for_state(svc, "error")
    st = svc.status()
    assert calls["zip"] == st["attempts"] == 5     # five tries, then the truth
    assert st["attempt"] == 5


def test_retry_after_a_spent_run_starts_a_fresh_attempt_count(tmp_path):
    """What the popup's 'Try again' button drives: a second begin_apply from
    the error state, which must be accepted and must reset the counter."""
    fails = {"left": 99}
    svc, root, calls = _svc_on_flaky(tmp_path, fails)
    svc.begin_apply(lambda: None)
    assert _wait_for_state(svc, "error")
    fails["left"] = 0                             # the network comes back
    done = threading.Event()
    assert svc.begin_apply(done.set)["state"] == "downloading"
    assert done.wait(timeout=10)
    assert (root / "SM64Trainer.exe").read_bytes() == b"EXE-V2"
    assert svc.status()["attempt"] == 1
    assert calls["zip"] > 5                       # 5 spent, then a fresh one


def test_a_failed_restart_is_never_retried(tmp_path):
    """The files ARE swapped by then — going round again would re-download and
    re-apply what is already installed."""
    svc, root, calls = _svc_on_flaky(tmp_path, {"left": 0})

    def restart_boom():
        raise RuntimeError("relaunch failed")

    svc.begin_apply(restart_boom)
    assert _wait_for_state(svc, "error")
    assert svc.status()["attempt"] == 1           # it stopped after the swap
    assert calls["zip"] > 0
    assert (root / "SM64Trainer.exe").read_bytes() == b"EXE-V2"

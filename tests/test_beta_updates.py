"""Player-facing beta preference, forward upgrades and repair boundaries."""
import hashlib
import json
import threading
import time
import urllib.error

import pytest

from sm64_events.core.release_feed import ReleaseFeed
from sm64_events.core.update_apply import (BACKUP_DIR, JOURNAL_NAME, apply_plan,
                                         confirm_startup, sweep_backup)
from sm64_events.core.update_plan import parse_manifest, validate_release_manifest
from sm64_events.core.update_policy import installed_floor, read_policy, record_installed, write_policy
from sm64_events.core.updater import UpdateService, check_for_update
from test_updater import (FULL_ASSETS, LATEST, RELEASES, _Resp, _fake_http,
                          _fake_release, _release_json)


def release(tag, **flags):
    return {**json.loads(_release_json(tag, FULL_ASSETS)),
            "prerelease": "-" in tag, **flags}


def feed_http(rows, latest="v1.0.0"):
    return _fake_http({LATEST: json.dumps(release(latest)).encode(),
                       RELEASES: json.dumps(rows).encode()})


@pytest.mark.parametrize("current,stable,beta,opt_in,expected", [
    ("1.8.0", "1.8.1", "1.9.0-beta.2", False, "1.8.1"),
    ("1.8.0", "1.8.1", "1.9.0-beta.2", True, "1.9.0-beta.2"),
    ("1.9.0-beta.2", "1.9.0", "1.9.0-beta.2", True, "1.9.0"),
    ("1.9.0", "1.9.0", "1.8.5-beta.1", True, None),
    ("1.9.0-beta.2", "1.8.5", "1.9.0-beta.2", True, None),
    ("1.9.0-beta.2", "1.8.5", "1.9.0-beta.3", False, None),
    ("1.9.0-beta.2", "1.9.0", "1.10.0-beta.1", True, "1.10.0-beta.1"),
])
def test_actual_discovery_obeys_approved_ordering(current, stable, beta, opt_in, expected):
    found = check_for_update(current, http=feed_http([release("v" + beta), release("v" + stable)],
                                                   latest="v" + stable), include_beta=opt_in)
    assert (found.version if found else None) == expected


def test_list_head_can_beat_latest_backport():
    found = check_for_update("1.7.0", http=feed_http([release("v1.9.0"), release("v1.8.5")],
                                                   latest="v1.8.5"))
    assert found.version == "1.9.0"


def test_beta_failure_keeps_stable_update():
    found = check_for_update("1.0.0", include_beta=True,
                            http=_fake_http({LATEST: json.dumps(release("v1.1.0")).encode()}))
    assert found.version == "1.1.0"


def test_feed_paginates_and_revalidates_with_etags():
    calls = []
    page2 = RELEASES + "&page=2"
    first = [release("v1.0.0") for _ in range(100)]
    docs = {LATEST: release("v1.0.0"), RELEASES: first, page2: [release("v1.9.0-beta.8")]}

    def http(req):
        calls.append((req.full_url, req.get_header("If-none-match")))
        if req.get_header("If-none-match"):
            raise urllib.error.HTTPError(req.full_url, 304, "Not modified", {}, None)
        return _Resp(json.dumps(docs[req.full_url]).encode(), {"ETag": '"revision-1"'})

    feed = ReleaseFeed(http=http, repo="griffinbeels/SM64-Trainer", api_base="https://api.github.com")
    for _ in range(2):
        assert check_for_update("1.0.0", include_beta=True, feed=feed).version == "1.9.0-beta.8"
    assert len(calls) == 6
    assert all(etag == '"revision-1"' for _, etag in calls[3:])


def test_partial_pagination_does_not_claim_a_beta_head():
    http = _fake_http({LATEST: json.dumps(release("v1.1.0")).encode(),
                       RELEASES: json.dumps([release("v1.9.0-beta.1")] * 100).encode()})
    assert check_for_update("1.0.0", http=http, include_beta=True).version == "1.1.0"


def test_policy_merges_skip_and_never_lowers_installation_floor(tmp_path):
    path = tmp_path / "profile/data/update_state.json"
    assert read_policy(path)["receive_beta_updates"] is False
    write_policy(path, receive_beta_updates=True, skipped="1.9.0-beta.2")
    record_installed(path, "1.9.0-beta.2")
    record_installed(path, "1.8.5")
    assert installed_floor(path, "1.8.5") == "1.9.0-beta.2"
    assert read_policy(path)["skipped"] == "1.9.0-beta.2"
    record_installed(path, "1.9.0")
    assert installed_floor(path, "1.8.5") == "1.9.0"
    assert read_policy(path)["receive_beta_updates"] is True


def service(tmp_path, routes, *, current="1.0.0", frozen=True):
    root = tmp_path / "app"
    root.mkdir()
    exe = root / "SM64Trainer.exe"
    exe.write_bytes(b"OLD")
    return UpdateService(current_version=current, http=_fake_http(routes), exe_path=exe,
                         frozen=frozen, state_path=tmp_path / "profile/data/update_state.json",
                         retry_delays=())


def test_enable_is_immediate_and_optout_invalidates_popup(tmp_path):
    routes = _fake_release(tmp_path / "release", "v1.9.0-beta.2", {"SM64Trainer.exe": b"BETA"})
    routes[RELEASES] = json.dumps([release("v1.9.0-beta.2")]).encode()
    routes[LATEST] = json.dumps(release("v1.0.0")).encode()
    svc = service(tmp_path, routes)
    assert not svc.status()["update_available"]
    assert svc.set_beta_updates(True)["latest"] == "1.9.0-beta.2"
    assert svc.beta_preferences()["receive_beta_updates"] is True
    svc.skip("1.9.0-beta.2")
    assert svc.set_beta_updates(False)["update_available"] is False
    assert svc.begin_apply(lambda: pytest.fail("must not restart"))["state"] == "error"
    assert svc.status()["skipped"] == "1.9.0-beta.2"
    assert svc._exe.read_bytes() == b"OLD"


def test_preferences_work_in_source_without_network_or_file_swap(tmp_path):
    svc = service(tmp_path, {}, frozen=False)
    assert svc.set_beta_updates(True)["receive_beta_updates"] is True
    assert svc.status()["update_available"] is False
    assert svc.begin_apply(lambda: None)["state"] == "error"


def test_offer_rejects_well_hashed_manifest_for_another_release(tmp_path):
    routes = _fake_release(tmp_path / "release", "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    doc = json.loads(routes["https://dl/manifest.json"])
    doc["version"] = "1.9.0"
    data = json.dumps(doc).encode()
    routes["https://dl/manifest.json"] = data
    routes["https://dl/manifest.sha"] = hashlib.sha256(data).hexdigest().encode()
    assert service(tmp_path, routes).status()["update_available"] is False


def test_manifest_requires_boot_and_capture_essentials(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    doc = json.loads(routes["https://dl/manifest.json"])
    doc["files"] = [entry for entry in doc["files"] if not entry["path"].endswith("python314.dll")]
    with pytest.raises(ValueError, match="essential"):
        validate_release_manifest(parse_manifest(json.dumps(doc)), "2.0.0")


def test_explicit_same_version_repair_refetches_corrupt_files(tmp_path):
    routes = _fake_release(tmp_path / "release", "v1.0.0", {"SM64Trainer.exe": b"NEW"})
    svc = service(tmp_path, routes)
    assert not svc.status()["update_available"]
    result = svc.status(force=True, repair=True)
    assert result["repair"] is True and result["latest"] == "1.0.0"
    assert svc.status()["repair"] is True
    done = threading.Event()
    assert svc.begin_apply(done.set, repair=True)["state"] == "downloading"
    assert done.wait(3)
    assert svc._exe.read_bytes() == b"NEW"


def test_play_resuming_during_download_prevents_the_swap(tmp_path):
    routes = _fake_release(tmp_path / "release", "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    svc = service(tmp_path, routes)
    checks = iter([None, "Pause the trainer or close Project64 before updating."])
    svc.begin_apply(lambda: pytest.fail("must not restart"), can_apply=lambda: next(checks))
    deadline = time.monotonic() + 3
    while svc._state != "error" and time.monotonic() < deadline:
        time.sleep(.01)
    assert "Pause the trainer" in svc.status()["error"]
    assert svc._exe.read_bytes() == b"OLD"
    assert not (svc._exe.parent / JOURNAL_NAME).exists()


def test_failed_rollback_and_orphaned_backup_survive_cleanup(tmp_path):
    root = tmp_path / "app"
    backup = root / BACKUP_DIR
    backup.mkdir(parents=True)
    (backup / "SM64Trainer.exe").write_bytes(b"ONLY-USABLE-EXE")
    assert not sweep_backup(root, attempts=1)
    doc = {"state": "rolled_back", "replace": ["SM64Trainer.exe"], "delete": [],
           "added": [], "rollback_failures": 1}
    (root / JOURNAL_NAME).write_text(json.dumps(doc))
    assert not confirm_startup(root)
    assert not sweep_backup(root, attempts=1)
    assert (backup / "SM64Trainer.exe").read_bytes() == b"ONLY-USABLE-EXE"


def test_second_update_cannot_overwrite_unconfirmed_recovery(tmp_path):
    root = tmp_path / "app"
    stage = tmp_path / "stage"
    root.mkdir(); stage.mkdir()
    (root / "SM64Trainer.exe").write_bytes(b"OLD")
    (stage / "SM64Trainer.exe").write_bytes(b"NEW")
    apply_plan(root, stage, replace=["SM64Trainer.exe"], delete=[])
    (stage / "SM64Trainer.exe").write_bytes(b"NEWER")
    with pytest.raises(RuntimeError, match="previous update"):
        apply_plan(root, stage, replace=["SM64Trainer.exe"], delete=[])
    assert (root / BACKUP_DIR / "SM64Trainer.exe").read_bytes() == b"OLD"


@pytest.mark.parametrize("path", [".UPDATE_BACKUP/only.exe", "CON.dll", "_internal/AUX", "safe.dll.", "safe.dll "])
def test_manifest_rejects_windows_file_aliases(tmp_path, path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    doc = json.loads(routes["https://dl/manifest.json"])
    doc["files"][0]["path"] = path
    with pytest.raises(ValueError, match="unsafe manifest"):
        parse_manifest(json.dumps(doc))


def test_manifest_rejects_case_insensitive_duplicate_paths(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    doc = json.loads(routes["https://dl/manifest.json"])
    doc["files"].append({**doc["files"][0], "path": doc["files"][0]["path"].upper()})
    with pytest.raises(ValueError, match="more than once"):
        parse_manifest(json.dumps(doc))


def test_concurrent_force_checks_share_one_feed_lookup(tmp_path):
    routes = _fake_release(tmp_path / "release", "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    svc = service(tmp_path, routes)
    started, release_call = threading.Event(), threading.Event()
    requests = []
    original = svc._feed.http

    def http(req):
        requests.append(req.full_url)
        if req.full_url == LATEST:
            started.set()
            assert release_call.wait(3)
        return original(req)

    svc._feed.http = http
    first = threading.Thread(target=lambda: svc.status(force=True))
    second = threading.Thread(target=lambda: svc.status(force=True))
    first.start()
    assert started.wait(3)
    second.start()
    time.sleep(.02)
    release_call.set()
    first.join(3); second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert requests.count(LATEST) == 1


def test_offline_check_reports_unknown_instead_of_up_to_date(tmp_path):
    status = service(tmp_path, {}).status()
    assert status["update_available"] is False
    assert "connection" in status["error"]


def test_restart_failure_reports_installed_files_and_requires_relaunch(tmp_path):
    routes = _fake_release(tmp_path / "release", "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    svc = service(tmp_path, routes)

    def restart():
        raise RuntimeError("restart failed")

    svc.begin_apply(restart)
    deadline = time.monotonic() + 3
    while svc._state != "error" and time.monotonic() < deadline:
        time.sleep(.01)
    assert svc.status()["files_changed"] is True
    assert svc._exe.read_bytes() == b"NEW"
    assert "Relaunch" in svc.begin_apply(lambda: None)["error"]


def test_old_cleanup_cannot_erase_a_new_updates_recovery(tmp_path, monkeypatch):
    from sm64_events.core import update_apply
    root, first, second = tmp_path / "app", tmp_path / "first", tmp_path / "second"
    for path in (root, first, second):
        path.mkdir()
    (root / "SM64Trainer.exe").write_bytes(b"V1")
    (first / "SM64Trainer.exe").write_bytes(b"V2")
    (second / "SM64Trainer.exe").write_bytes(b"V3")
    apply_plan(root, first, replace=["SM64Trainer.exe"], delete=[])
    assert confirm_startup(root)
    reached_cleanup, resume_cleanup, applied = threading.Event(), threading.Event(), threading.Event()
    failures = []
    rmtree = update_apply.shutil.rmtree

    def delayed_cleanup(path, **kwargs):
        reached_cleanup.set()
        assert resume_cleanup.wait(3)
        return rmtree(path, **kwargs)

    monkeypatch.setattr(update_apply.shutil, "rmtree", delayed_cleanup)
    sweep = threading.Thread(target=lambda: sweep_backup(root, attempts=1))

    def update():
        try:
            apply_plan(root, second, replace=["SM64Trainer.exe"], delete=[])
            applied.set()
        except Exception as error:  # noqa: BLE001 - recorded for the main thread to assert
            failures.append(error)

    swap = threading.Thread(target=update)
    sweep.start()
    assert reached_cleanup.wait(3)
    swap.start()
    assert not applied.wait(.04)
    resume_cleanup.set()
    sweep.join(3); swap.join(3)
    assert not sweep.is_alive() and not swap.is_alive() and not failures
    assert applied.is_set()
    assert (root / BACKUP_DIR / "SM64Trainer.exe").read_bytes() == b"V2"
    assert update_apply.read_journal(root)["state"] == "done"
    assert not update_apply.read_journal(root).get("healthy_startup")


def test_explicit_repair_of_installed_beta_keeps_beta_opt_out(tmp_path):
    tag = "v1.9.0-beta.2"
    routes = _fake_release(tmp_path / "release", tag, {"SM64Trainer.exe": b"REPAIRED"})
    routes[LATEST] = json.dumps(release("v1.8.5")).encode()
    routes[RELEASES] = json.dumps([release("v1.9.0-beta.3"), release(tag)]).encode()
    svc = service(tmp_path, routes, current=tag[1:])
    assert svc.beta_preferences()["receive_beta_updates"] is False
    assert svc.status()["update_available"] is False
    assert svc.status(force=True, repair=True)["latest"] == tag[1:]
    finished = threading.Event()
    assert svc.begin_apply(finished.set, repair=True)["state"] == "downloading"
    assert finished.wait(3)
    assert svc._exe.read_bytes() == b"REPAIRED"
    assert svc.beta_preferences()["receive_beta_updates"] is False


def test_delayed_startup_cleanup_cannot_confirm_a_later_update(tmp_path, monkeypatch):
    from sm64_events.core import updater, update_apply
    svc = service(tmp_path, {}, current="2.0.0")
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    (first / "SM64Trainer.exe").write_bytes(b"V2")
    (second / "SM64Trainer.exe").write_bytes(b"V3")
    apply_plan(svc._exe.parent, first, replace=["SM64Trainer.exe"], delete=[])
    deferred = []

    class DelayedThread:
        def __init__(self, *, target, **kwargs):
            deferred.append(target)

        def start(self):
            pass

    monkeypatch.setattr(updater.threading, "Thread", DelayedThread)
    svc.startup_maintenance()
    assert update_apply.read_journal(svc._exe.parent)["healthy_startup"]
    apply_plan(svc._exe.parent, second, replace=["SM64Trainer.exe"], delete=[])
    deferred[0]()
    assert (svc._exe.parent / BACKUP_DIR / "SM64Trainer.exe").read_bytes() == b"V2"
    assert not update_apply.read_journal(svc._exe.parent).get("healthy_startup")


@pytest.mark.parametrize("flag", [{"withdrawn": True}, {"draft": True},
                                {"body": "<!-- sm64-withdrawn -->"}])
def test_stale_latest_cannot_resurrect_a_withdrawn_list_release(flag):
    http = feed_http([release("v1.1.0", **flag), release("v1.0.1")], latest="v1.1.0")
    assert check_for_update("1.0.0", http=http).version == "1.0.1"


def test_partial_history_cannot_discard_an_observed_withdrawal():
    rows = [release("v1.1.0", withdrawn=True)] + [release("v1.0.0")] * 99
    assert check_for_update("1.0.0", http=feed_http(rows, latest="v1.1.0")) is None


def test_a_beta_build_opts_its_install_into_beta_updates_once(tmp_path, monkeypatch):
    """His rule (round 50): the beta build he sends a tester already receives
    later betas and production; production builds stay opted out until the
    player ticks Settings > Receive beta updates, and a player's own choice
    is never overridden."""
    from sm64_events.core import build_channel
    from sm64_events.core.update_policy import adopt_channel_default
    beta = build_channel.BuildChannel(channel="beta", build_id="tester-beta", installation="normal")
    stable = build_channel.BuildChannel(channel="stable", build_id="", installation="normal")
    routes = _fake_release(tmp_path / "release", "v1.9.0-beta.3", {"SM64Trainer.exe": b"BETA"})
    routes[RELEASES] = json.dumps([release("v1.9.0-beta.3")]).encode()
    routes[LATEST] = json.dumps(release("v1.0.0")).encode()
    monkeypatch.setattr(build_channel, "current_build", lambda: beta)
    svc = service(tmp_path, routes, current="1.9.0-beta.2")
    assert svc.beta_preferences()["receive_beta_updates"] is True
    assert svc.status()["latest"] == "1.9.0-beta.3"  # the next published beta reaches the tester
    # Unticked once, a later start of the beta build keeps the player's choice.
    svc.set_beta_updates(False)
    assert adopt_channel_default(tmp_path / "profile/data/update_state.json", "beta")["receive_beta_updates"] is False
    # A production build never opts anyone in.
    other = tmp_path / "production"
    other.mkdir()
    monkeypatch.setattr(build_channel, "current_build", lambda: stable)
    assert service(other, routes).beta_preferences()["receive_beta_updates"] is False

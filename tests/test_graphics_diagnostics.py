"""Offline diagnostics preserve evidence and never make a reader request frames."""
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import graphics_diagnostics as G  # noqa: E402


def test_tail_is_bounded_and_missing_is_explicit(tmp_path):
    path = tmp_path / "plugin.log"
    path.write_bytes(b"old secret history\n" + b"x" * G.TAIL_BYTES)
    result = G.file_tail(path)
    assert result["offset"] == len(b"old secret history\n")
    assert result["text"] == "x" * G.TAIL_BYTES
    assert result["tail_sha256"] and not result["error"]
    assert G.file_tail(tmp_path / "missing.log")["error"]


def test_startup_requires_matching_process_lifetime_not_just_loaded_dll():
    now = datetime.now(UTC).timestamp()
    stamp = datetime.fromtimestamp(now - 10, UTC).isoformat(timespec="milliseconds")
    logs = [{"path": "plugin.log", "text": f"{stamp} pid=12 tid=34 build=abc event=init_begin wrapper_version=2"}]
    process = [{"pid": 12, "create_time": now - 20}]
    assert G.wrapper_identity(logs, process)[0]["matches_current_process_start"]
    process[0]["create_time"] = now - 5
    assert not G.wrapper_identity(logs, process)[0]["matches_current_process_start"]
    assert G.wrapper_identity([], process) == []


@pytest.mark.parametrize("runtime", ["matching", "old_build", "old_birth", "closed", "absent", "unmapped", "unknown_birth"])
def test_identity_separates_candidate_bytes_from_loaded_evidence(tmp_path, runtime):
    build = "a" * 64 + "-gpu-runtime"
    candidate = tmp_path / "candidate.dll"
    candidate.write_bytes(b"binary prefix\x00" + build.encode() + b"\x00")
    expected = G.file_fingerprint(candidate)
    assert expected["build_ids"] == [build]
    assert expected["bytes"] == candidate.stat().st_size
    birth = 1_700_000_000
    ticks = (birth + 11644473600) * 10_000_000
    process = {"pid": 77, "create_time": birth, "graphics_modules": ["sm64_trainer_gfx.dll"]}
    status = {"producer_pid": 77, "state": 4, "producer_created_lo": ticks & 0xFFFFFFFF,
              "producer_created_hi": ticks >> 32, "build_id": build}
    if runtime == "old_build":
        status["build_id"] = "b" * 64 + "-gpu-runtime"
    if runtime == "old_birth":
        process["create_time"] += 10
    if runtime == "unknown_birth":
        process["create_time"] = None
    if runtime == "closed":
        status["state"] = 0
    if runtime == "unmapped":
        process["graphics_modules"] = []
    result = G.installation_identity(expected, expected, [expected], [process],
                                     {"status": None if runtime == "absent" else status})
    assert result["installed"][0]["bytes_match_candidate"] is True
    assert result["bundle_bytes_match_candidate"] is True
    assert result["loaded"][0]["source_build_matches_candidate"] is (
        True if runtime == "matching" else False if runtime == "old_build" else None)


def test_equal_build_label_does_not_hide_different_binary_bytes(tmp_path):
    label = ("a" * 64 + "-gpu-runtime").encode() + b"\x00"
    candidate, installed = tmp_path / "candidate.dll", tmp_path / "installed.dll"
    candidate.write_bytes(label + b"compiler one")
    installed.write_bytes(label + b"compiler two")
    expected, actual = G.file_fingerprint(candidate), G.file_fingerprint(installed)
    assert expected["build_ids"] == actual["build_ids"]
    result = G.installation_identity(expected, expected, [actual], [], {"status": None})
    assert result["installed"][0]["bytes_match_candidate"] is False
    assert G._same_bytes(expected, G.file_fingerprint(tmp_path / "missing.dll")) is None


def test_collection_is_read_only_and_copies_rotated_logs(tmp_path, monkeypatch):
    folder = tmp_path / "PJ64"
    plugin = folder / "Plugin"
    plugin.mkdir(parents=True)
    log = plugin / "sm64_trainer_gfx.log"
    log.write_bytes(b"original log\r\nnext line\r\n")
    (plugin / "sm64_trainer_gfx.dll").write_bytes(b"installed wrapper")
    (plugin / "sm64_trainer_gfx.log.1").write_text("older log")
    monkeypatch.setattr(G, "control_snapshot", lambda name: {"status": None, "error": "absent"})
    monkeypatch.setattr(G, "find_processes", lambda: [])
    result = G.collect(tmp_path / "capture", pj64_dir=folder)
    assert len(result["samples"]) == 1 and "header" not in result["samples"][0]
    assert result["samples"][0]["control"] == {"status": None, "error": "absent"}
    assert len(result["logs"]) == 2
    assert len(result["wrapper_files_on_disk"]) == 1
    assert result["wrapper_files_on_disk"][0]["sha256"]
    assert log.read_bytes() == b"original log\r\nnext line\r\n"
    for row in result["logs"]:
        copied = (tmp_path / "capture" / row["captured_as"]).read_bytes()
        assert hashlib.sha256(copied).hexdigest() == row["tail_sha256"]
    assert json.loads((tmp_path / "capture/report.json").read_text())["logs"] == result["logs"]
    with pytest.raises(FileExistsError):
        G.collect(tmp_path / "capture", pj64_dir=folder)
    with pytest.raises(ValueError):
        G.collect(tmp_path / "another", seconds=31)

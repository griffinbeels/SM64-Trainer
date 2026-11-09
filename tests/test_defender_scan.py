"""Microsoft Defender gates our natives without ever changing Defender (tools/defender_scan.py).

A fake runner stands in for MpCmdRun; the real scan of a fresh build was run
by hand (its result is in the clean-behavior design record's handoff).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import defender_scan  # noqa: E402

THREAT = "Scanning C:\\x.dll found 1 threats.\nThreat                  : Trojan:Win32/Bearfoos.A!ml\n"


@pytest.fixture
def built(tmp_path):
    path = tmp_path / "GLideN64_SM64Trainer.dll"
    path.write_bytes(b"MZ fixture")
    return path


def test_each_file_is_scanned_alone_and_never_remediated(built):
    commands = []
    verdicts = defender_scan.scan([built], scanner=Path("C:/Defender/MpCmdRun.exe"),
                                  runner=lambda command: commands.append(command) or (0, ""))
    assert verdicts == [defender_scan.Verdict(built, "clean", "no threat")]
    command = commands[0]
    assert command[0] == str(Path("C:/Defender/MpCmdRun.exe"))
    assert command[1:] == ["-Scan", "-ScanType", "3", "-File", str(built.resolve()), "-DisableRemediation"]


def test_a_threat_stops_naming_the_file_and_the_verdict(built):
    said = []
    with pytest.raises(defender_scan.ThreatFound) as found:
        defender_scan.require_clean([built], runner=lambda command: (2, THREAT), log=said.append)
    assert str(built) in str(found.value) and "Trojan:Win32/Bearfoos.A!ml" in str(found.value)
    assert found.value.verdicts[0].path == built
    assert any(line.startswith("  STOP:") for line in said)


def test_a_scan_that_did_not_finish_warns_and_only_a_strict_gate_refuses(built):
    said = []
    verdicts = defender_scan.require_clean([built], runner=lambda command: (1, "engine unavailable"),
                                           log=said.append)
    assert verdicts[0].state == "failed" and any("could not scan" in line for line in said)
    with pytest.raises(RuntimeError, match="could not scan"):
        defender_scan.require_clean([built], strict=True, runner=lambda command: (1, ""), log=lambda _: None)


def test_a_file_real_time_protection_removed_is_not_reported_clean(tmp_path):
    gone = tmp_path / "gone.dll"
    verdict, = defender_scan.scan([gone], runner=lambda command: pytest.fail("scanned a missing file"))
    assert verdict.state == "failed" and "gone" in verdict.detail


def test_without_a_scanner_it_says_so_and_the_build_continues(built, monkeypatch):
    monkeypatch.setattr(defender_scan, "find_scanner", lambda: None)
    said = []
    verdicts = defender_scan.require_clean([built], strict=True, log=said.append)
    assert verdicts[0].state == "unavailable"
    assert any("not scanned" in line for line in said)


@pytest.mark.skipif(sys.platform != "win32", reason="Defender is Windows'")
def test_this_machine_has_a_scanner_to_run():
    scanner = defender_scan.find_scanner()
    assert scanner is not None and scanner.name.lower() == "mpcmdrun.exe" and scanner.is_file()


# -- the release and beta builders refuse a flagged file ----------------------------

def test_a_release_build_scans_our_own_files_and_refuses_a_flagged_one(tmp_path, monkeypatch):
    import build_exe
    app = tmp_path / "SM64Trainer"
    for path in build_exe.our_files(app, "SM64Trainer"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MZ fixture")
    scanned = []
    monkeypatch.setattr(defender_scan, "scan", lambda paths, **kwargs: scanned.extend(Path(p).name for p in paths) or [
        defender_scan.Verdict(Path(paths[0]), "threat", "Threat : Trojan:Win32/Bearfoos.A!ml"),
        *(defender_scan.Verdict(Path(p), "clean", "no threat") for p in paths[1:])])
    with pytest.raises(defender_scan.ThreatFound):
        build_exe.scan_built(app, "SM64Trainer")
    from sm64_events.core.update_plan import LAB_NATIVES
    assert scanned == ["SM64Trainer.exe", "sm64_trainer_gfx.dll", "SM64GpuEncoderV1.dll", *LAB_NATIVES]


def test_a_beta_build_refuses_a_flagged_native_before_building(tmp_path, monkeypatch):
    import build_beta
    bundle = tmp_path / "plugin"
    bundle.mkdir()
    (bundle / "sm64_trainer_gfx.dll").write_bytes(b"MZ fixture")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fixture")
    monkeypatch.setattr(build_beta.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setitem(sys.modules, "build_natives", SimpleNamespace(
        ensure=lambda **kwargs: {}, bundle_files=lambda lab=True: [bundle / "sm64_trainer_gfx.dll"]))
    monkeypatch.setattr(defender_scan, "scan", lambda paths, **kwargs: [
        defender_scan.Verdict(Path(paths[0]), "threat", "Threat : Trojan:Win32/Bearfoos.A!ml")])
    with pytest.raises(defender_scan.ThreatFound, match="sm64_trainer_gfx.dll"):
        build_beta.check_prerequisites(str(ffmpeg))


def test_the_command_line_exits_two_on_a_threat(built, monkeypatch, capsys):
    monkeypatch.setattr(defender_scan, "scan", lambda paths, **kwargs: [
        defender_scan.Verdict(Path(paths[0]), "threat", "Threat : X")])
    assert defender_scan.main([str(built)]) == 2
    assert "STOP" in capsys.readouterr().out

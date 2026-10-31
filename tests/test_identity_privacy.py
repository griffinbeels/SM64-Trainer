"""No account or computer name leaves the machine, whatever text carries it.

Griffin (2026-10-30): "we should also intentionally purge any identifying file
paths (like a user's username) from the filepath / files / any debug logs /
etc". Each test plants a distinctive account name and computer name in the
environment, then in paths, window titles, the Project64 folder name,
exception text and a plugin log line, and reads the bytes that would leave.
"""
import getpass
import json
from pathlib import Path
import platform
import re
import time

import pytest
from sentry_sdk.envelope import Envelope

from sm64_events.core import diagnostics, error_reporting as reporting, identity_privacy
from sm64_events.core.diagnostic_snapshot import DiagnosticSnapshot
from sm64_events.core.error_reporting_privacy import safe_breadcrumb, safe_event, safe_log, safe_log_item
from sm64_events.core.setup_profile import folder_name
from sm64_events.core.silent_failures import MESSAGES, SilentFailureMonitor
from tests.test_diagnostic_evidence import native_line
from tests.test_diagnostics import _healthy_kwargs
from types import SimpleNamespace as NS

USER, COMPUTER, SHORT = "zzqUserTok", "ZZQ-PC", "zq"
LEAK = re.compile(r"zzqusertok|zzq-pc", re.I)
HOME = f"C:\\Users\\{USER}"
PLANTED = (f"PermissionError: [WinError 5] Access is denied: '{HOME}\\Games\\PJ64\\Plugin\\GLideN64.ini' "
           f"(window 'Usamune {USER}.z64 - Project64' on {COMPUTER.lower()}; owner {USER.upper()})")


@pytest.fixture
def identity(monkeypatch):
    for name in ("USER", "LOGNAME", "HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USERNAME", USER)
    monkeypatch.setenv("USERPROFILE", HOME)
    monkeypatch.setenv("COMPUTERNAME", COMPUTER)
    monkeypatch.setenv("USERDOMAIN", COMPUTER)
    monkeypatch.setattr(getpass, "getuser", lambda: USER)
    monkeypatch.setattr(platform, "node", lambda: COMPUTER)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(HOME)))
    identity_privacy.reset()
    yield
    identity_privacy.reset()


def test_the_pass_replaces_both_names_in_any_case_and_short_names_only_in_paths(identity, monkeypatch):
    text = identity_privacy.purge(PLANTED)
    assert not LEAK.search(text)
    assert "[user]" in text and "[computer]" in text and "GLideN64.ini" in text
    assert identity_privacy.purge(text) == text                     # a second pass changes nothing
    monkeypatch.setenv("USERNAME", SHORT)
    monkeypatch.setattr(getpass, "getuser", lambda: SHORT)
    monkeypatch.setenv("USERPROFILE", f"C:\\Users\\{SHORT}")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(f"C:\\Users\\{SHORT}")))
    identity_privacy.reset()
    assert identity_privacy.purge(f"C:\\Users\\{SHORT}\\pj64 zqxy unzipped") == "C:\\Users\\[user]\\pj64 zqxy unzipped"


def test_a_folder_name_holding_an_identity_is_unnamed(identity):
    assert folder_name(f"{USER}'s PJ64") == "unnamed"
    assert folder_name(f"{COMPUTER} copy") == "unnamed"
    assert folder_name("01-stock-vanilla") == "01-stock-vanilla"


def planted_check():
    return {"version": 1, "baseline": {"recorded": "2026-10-30", "source": "x"}, "rom": None, "rdb": "missing",
            "folder": {"name": f"{USER}'s PJ64", "location": "other", "running": True},
            "flagged": ["audio_plugin"], "required_differs": 0, "unknown": 0,
            "items": [{"id": "audio_plugin", "importance": "recommended", "status": "differs", "shared": True,
                       "yours": f"{USER}_audio.dll (sha256 0123456789ab)", "tested": "Azimer's Audio",
                       "fix": "Pick it.", "link": "https://wermi.neocities.org/emuguide/azi/"}]}


def planted_snapshot():
    return {"captured_utc": "2026-10-30T12:00:00+00:00",
            "capture": {"recorder": {"recording": True, "window_title": f"Usamune {USER}.z64 - Project64",
                                     "publication_error": PLANTED}},
            "ghost": {"enabled": True, "native": {"displayed": False, "draw_refusal": PLANTED}},
            "setup": {"state": "active", "pj64_dir": f"{HOME}\\Games\\{USER}'s PJ64", "check": planted_check(),
                      "refresh_error": PLANTED},
            "native_log_tails": [{"source": "wrapper", "file": f"{HOME}\\pj64\\sm64_trainer_gfx.log",
                                  "tail": native_line(details=f"reason=5 path={HOME}\\x.ini host={COMPUTER}")
                                  + f"free text by {USER} on {COMPUTER}\n"}]}


def planted_report(tmp_path):
    kwargs = _healthy_kwargs(tmp_path)
    with kwargs["log_file"].open("a", encoding="utf-8") as log:
        log.write(f"\n2026-10-30T12:00:00Z WARNING sm64.setup {PLANTED}"
                  f"\n2026-10-30T12:00:01Z ERROR sm64.replay Traceback for {USER} on {COMPUTER}"
                  f'\n  File "{HOME}\\code\\sm64_events\\x.py", line 3, in run')
    return diagnostics.build_report(**kwargs, snapshot=planted_snapshot, setup_check=planted_check,
                                    setup=lambda: {"pj64_dir": HOME, "state": PLANTED})


def test_an_exported_debug_report_carries_neither_name(identity, tmp_path):
    path = diagnostics.write_report(tmp_path / "reports", planted_report(tmp_path))
    written = path.read_bytes().decode("utf-8")
    assert "## Setup compared with the tested setup" in written and "audio_plugin" in written
    assert not LEAK.search(written), LEAK.findall(written)


def test_logs_and_breadcrumbs_carry_neither_name(identity):
    log = safe_log({"severity_number": 17, "severity_text": "warn", "body": PLANTED, "time_unix_nano": 1,
                    "attributes": {"logger.name": "sm64.setup", "code.file.path": f"{HOME}\\x.py",
                                   "sentry.message.template": f"{USER} on {COMPUTER}"}})
    item = safe_log_item({"timestamp": 1.0, "level": "warn", "body": PLANTED,
                          "attributes": {"logger.name": {"value": "sm64.setup"},
                                         "sentry.severity_number": {"value": 17}}},
                         run_trace="0" * 32)
    crumb = safe_breadcrumb({"category": "sm64.setup", "message": PLANTED, "level": "info"})
    batch = json.dumps([log, item, crumb])
    assert log["body"] and item["body"] and crumb["message"]
    assert not LEAK.search(batch), LEAK.findall(batch)


@pytest.fixture
def reporter(tmp_path, monkeypatch):
    monkeypatch.setattr("sm64_events.core.error_reporting_transport.Delivery", lambda dsn: lambda payload: (False, 0))
    reporting.configure(dsn="https://public@example.invalid/123", release="synthetic-identity",
                        environment="test", directory=tmp_path / "spool")
    yield tmp_path / "spool"
    reporting.set_diagnostic_source(None)
    reporting.shutdown()


def spooled(path, count=1):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        files = list(path.glob("*.envelope"))
        if len(files) >= count and all(Envelope.deserialize(f.read_bytes()).items for f in files):
            return files
        time.sleep(.01)
    return list(path.glob("*.envelope"))


def test_a_silent_failure_envelope_and_its_attached_report_carry_neither_name(identity, reporter, tmp_path):
    from tests.test_silent_failures import Harness
    snapshot = DiagnosticSnapshot(ghost=lambda: planted_snapshot()["ghost"],
                                  setup=lambda: planted_snapshot()["setup"],
                                  capture=lambda: planted_snapshot()["capture"],
                                  native=NS(snapshot=lambda _: {"available": False}, close=lambda: None))
    unbind_snapshot = reporting.set_diagnostic_source(snapshot.collect)
    unbind_report = reporting.set_report_source(lambda: planted_report(tmp_path))
    h = Harness()
    h.data["setup"]["pending"] = False
    h.watch = SilentFailureMonitor(lambda: h.data, clock=lambda: h.now, wall_clock=lambda: h.now,
                                   interval_s=1, grace_s=3)
    try:
        h.tick(count=5)
        deadline = time.monotonic() + 5
        report = None
        while time.monotonic() < deadline and report is None:
            for path in reporter.glob("*.envelope"):
                envelope = Envelope.deserialize(path.read_bytes())
                if envelope.get_event() and envelope.get_event().get("message") == MESSAGES["ghost"] \
                        and any(item.type == "attachment" for item in envelope.items):
                    report = path
            time.sleep(.02)
        assert report is not None, "no ghost envelope with its Debug report"
        envelope = Envelope.deserialize(report.read_bytes())
        attachment = next(item for item in envelope.items if item.type == "attachment").payload.get_bytes()
        assert b"## Setup compared with the tested setup" in attachment
        event = envelope.get_event()
        assert event["extra"]["diagnostics"]["setup"]["check"]["folder"]["name"] == "unnamed"
        for path in reporter.glob("*.envelope"):
            assert not LEAK.search(path.read_bytes().decode("utf-8", "replace")), path.name
    finally:
        unbind_report()
        unbind_snapshot()
        snapshot.close()


def test_a_direct_event_cannot_carry_either_name(identity):
    event = safe_event({"message": PLANTED, "logger": f"{USER}.logger",
                        "exception": {"values": [{"type": "OSError", "value": PLANTED}]},
                        "extra": {"diagnostics": planted_snapshot()}})
    assert not LEAK.search(json.dumps(event)), LEAK.findall(json.dumps(event))

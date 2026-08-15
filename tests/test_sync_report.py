import json

from sm64_events.sync.gates import Verdict
from sm64_events.sync.report import Report, report_path


def test_report_records_and_reloads(tmp_path):
    report = Report(tmp_path / "jp.json")
    assert report.status("address.global_timer") == "missing"
    report.record("address.global_timer", Verdict("verified", value=1),
                  "2026-08-15T00:00:00Z")
    again = Report(tmp_path / "jp.json").load()
    assert again.status("address.global_timer") == "verified"
    assert again.verdicts["address.global_timer"].value == 1
    on_disk = json.loads((tmp_path / "jp.json").read_text())
    assert on_disk["address.global_timer"]["at"] == "2026-08-15T00:00:00Z"
    assert not (tmp_path / "jp.tmp").exists()


def test_report_path_lives_under_data_version_sync(tmp_path):
    assert report_path("jp", tmp_path) == tmp_path / "version_sync" / "jp.json"
    assert report_path("us").as_posix().endswith("data/version_sync/us.json")

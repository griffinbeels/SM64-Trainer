"""The skip inventory has to be the thing that fails the run.

A guard that only *describes* the skips would be another place to read; this
pins that an undocumented skip actually sets a non-zero exit status, and that
a documented one does not.
"""
import types

import pytest

import conftest
import skip_inventory


def _report(nodeid, reason, skipped=True, wasxfail=False):
    report = types.SimpleNamespace(
        nodeid=nodeid, skipped=skipped,
        longrepr=("tests/x.py", 1, f"Skipped: {reason}"))
    if wasxfail:
        report.wasxfail = "known defect"
    return report


def _session(file_or_dir=(), keyword=""):
    option = types.SimpleNamespace(file_or_dir=list(file_or_dir), keyword=keyword)
    config = types.SimpleNamespace(option=option)
    return types.SimpleNamespace(config=config, exitstatus=0)


@pytest.fixture(autouse=True)
def clean_skip_log():
    saved = list(conftest._SKIPS)
    conftest._SKIPS.clear()
    yield
    conftest._SKIPS[:] = saved


def test_every_row_says_why_and_where():
    assert skip_inventory.ALLOWED
    for pattern, why, where in skip_inventory.ALLOWED:
        assert pattern.strip() and len(why) > 40 and where.strip(), pattern


def test_a_documented_skip_is_recognised_and_an_invented_one_is_not():
    assert skip_inventory.allowed_for(
        "h264_qsv is not usable on this machine (ffmpeg exit 1)") is not None
    assert skip_inventory.allowed_for("because I said so") is None


def test_an_undocumented_skip_fails_a_whole_suite_run(capsys):
    conftest.pytest_runtest_logreport(_report("tests/test_a.py::t", "because I said so"))
    session = _session()
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1
    assert "UNDOCUMENTED SKIPS" in capsys.readouterr().out


def test_a_documented_skip_leaves_the_run_alone():
    conftest.pytest_runtest_logreport(
        _report("tests/test_b.py::t", "h264_qsv is not usable on this machine"))
    session = _session()
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 0


def test_a_narrowed_run_is_free_to_skip_anything(monkeypatch):
    """Only a run claiming to have covered everything owes an account."""
    monkeypatch.delenv("SM64_SKIP_AUDIT", raising=False)
    conftest.pytest_runtest_logreport(_report("tests/test_c.py::t", "because I said so"))
    for session in (_session(file_or_dir=["tests/test_c.py"]), _session(keyword="c")):
        conftest.pytest_sessionfinish(session, 0)
        assert session.exitstatus == 0


def test_a_whole_module_skipped_during_collection_is_counted(capsys):
    """The dangerous kind: `allow_module_level=True` produces no test reports
    at all, which is how a browser file drops 321 tests without a trace. The
    first version of this guard watched only test reports and let a planted
    module skip through a whole-suite run reporting "0 undocumented"."""
    conftest.pytest_collectreport(_report("tests/test_browser_thing.py", "uilab not found"))
    session = _session()
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1
    assert "uilab not found" in capsys.readouterr().out


def test_an_xfail_is_not_counted_as_an_untested_path():
    conftest.pytest_runtest_logreport(
        _report("tests/test_d.py::t", "known defect", wasxfail=True))
    session = _session()
    conftest.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 0


def test_a_runner_only_reason_passes_on_a_runner_and_fails_here():
    """A GitHub runner has no machine-level harness; this desktop must, and a
    guard that skips here for that reason is a hole, not a gap."""
    reason = "the shared harness is not installed at /home/runner/.claude/harness/skills"
    assert skip_inventory.allowed_for(reason, runner=True) is not None
    assert skip_inventory.allowed_for(reason, runner=False) is None
    for pattern, why, where in skip_inventory.RUNNER_ONLY:
        assert pattern.strip() and len(why) > 30 and where.strip(), pattern

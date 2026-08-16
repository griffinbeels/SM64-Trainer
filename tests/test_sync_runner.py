"""Runner mechanics: ordering, skip-on-unmet-needs, atomic record, an
exception caught as failed, and the version-mismatch refusal.

Uses tiny LOCAL test gates (via the `isolated_gates` fixture) rather than the
real registry, because Track D's address/calibration gates are filled in by a
sibling worktree and may not exist here yet -- see
sync/feature_gates.py's module docstring for the same convention there. The
fixture saves and restores `sync.gates.GATES` so these tests cannot leave the
shared registry worse off than they found it, whatever else has (or has not)
registered into it this session.
"""
from pathlib import Path

import pytest

from sm64_events.memory.buffer import BufferMemory
from sm64_events.memory.layout import LayoutIncomplete
from sm64_events.sync import gates as G
from sm64_events.sync.report import Report
from sm64_events.sync.runner import (GateContext, default_server, run,
                                     summary, working_layout)


@pytest.fixture
def isolated_gates():
    saved = list(G.GATES)
    G.GATES.clear()
    yield G
    G.GATES.clear()
    G.GATES.extend(saved)


class _RomMem(BufferMemory):
    """BufferMemory (the N64Memory test double) plus a rom_header() the
    version probe can read -- what a GateContext's mem needs beyond the
    plain read_u8/u16/u32 protocol."""

    def __init__(self, version: str = "us"):
        super().__init__()
        self._version = version

    def rom_header(self) -> bytes:
        header = bytearray(0x40)
        header[0:4] = b"\x80\x37\x12\x40"
        header[0x20:0x20 + 14] = b"SUPER MARIO 64"
        header[0x3E] = {"us": 0x45, "jp": 0x4A}[self._version]
        return bytes(header)


def _stub_gate(gate_id: str, status: str = "verified", value=None,
              needs: tuple = (), evidence: str = "") -> G.Gate:
    def check(ctx):
        return G.Verdict(status, value=value, evidence=evidence or f"stub:{gate_id}")
    return G.Gate(gate_id, "version", "address", f"do the {gate_id} thing",
                 "it establishes the stubbed fact", check, needs=needs,
                 auto=True)


def _quiet(text: str) -> str:
    return ""


# --- run() mechanics ---------------------------------------------------

def test_run_writes_a_verified_gate_into_the_report_on_disk(tmp_path, isolated_gates):
    G.register(_stub_gate("address.global_timer", value=0x8032D5D4))
    report = run("us", _RomMem("us"), only="address.global_timer",
                prompt=_quiet, say=_quiet, report_root=tmp_path)
    assert report.status("address.global_timer") == "verified"
    on_disk = Report(tmp_path / "version_sync" / "us.json").load()
    assert on_disk.status("address.global_timer") == "verified"
    assert on_disk.verdicts["address.global_timer"].value == 0x8032D5D4


def test_a_gate_with_an_unmet_need_is_skipped_and_names_why(tmp_path, isolated_gates):
    G.register(
        _stub_gate("address.global_timer", status="failed"),
        G.Gate("feature.x", "star grab", "feature", "do x", "x works",
              lambda ctx: G.Verdict("verified"),
              needs=("address.global_timer",)))
    report = run("us", _RomMem("us"), prompt=_quiet, say=_quiet,
                report_root=tmp_path)
    assert report.status("address.global_timer") == "failed"
    assert report.status("feature.x") == "skipped"
    assert "address.global_timer" in report.verdicts["feature.x"].evidence


def test_an_exception_in_a_check_is_recorded_as_failed_not_fatal(tmp_path, isolated_gates):
    def boom(ctx):
        raise RuntimeError("boom")
    G.register(G.Gate("address.global_timer", "version", "address", "do it",
                      "it works", boom, auto=True))
    report = run("us", _RomMem("us"), prompt=_quiet, say=_quiet,
                report_root=tmp_path)
    assert report.status("address.global_timer") == "failed"
    assert "boom" in report.verdicts["address.global_timer"].evidence


def test_gates_run_in_needs_order(tmp_path, isolated_gates):
    order: list[str] = []
    G.register(
        G.Gate("b", "version", "address", "b", "b",
              lambda ctx: order.append("b") or G.Verdict("verified"),
              needs=("a",), auto=True),
        G.Gate("a", "version", "address", "a", "a",
              lambda ctx: order.append("a") or G.Verdict("verified"),
              auto=True))
    run("us", _RomMem("us"), prompt=_quiet, say=_quiet, report_root=tmp_path)
    assert order == ["a", "b"]


def test_a_version_mismatch_refuses(tmp_path, isolated_gates):
    G.register(_stub_gate("address.global_timer"))
    with pytest.raises(ValueError):
        run("us", _RomMem("jp"), prompt=_quiet, say=_quiet, report_root=tmp_path)


def test_only_version_rom_is_exempt_from_the_mismatch_refusal(tmp_path, isolated_gates):
    G.register(G.Gate("version.rom", "version", "address", "nothing to do",
                      "names the ROM", lambda ctx: G.Verdict("failed",
                      evidence="jp != us"), auto=True))
    report = run("us", _RomMem("jp"), only="version.rom", prompt=_quiet,
                say=_quiet, report_root=tmp_path)
    assert report.status("version.rom") == "failed"


def test_only_selects_a_whole_feature_by_name(tmp_path, isolated_gates):
    G.register(
        G.Gate("feature.a", "star grab", "feature", "a", "a",
              lambda ctx: G.Verdict("verified"), auto=True),
        G.Gate("feature.b", "death", "feature", "b", "b",
              lambda ctx: G.Verdict("verified"), auto=True))
    report = run("us", _RomMem("us"), only="star grab", prompt=_quiet,
                say=_quiet, report_root=tmp_path)
    assert report.status("feature.a") == "verified"
    assert report.status("feature.b") == "missing"


# --- working_layout() ---------------------------------------------------

def test_working_layout_fills_a_none_field_from_a_verified_report(tmp_path):
    report = Report(tmp_path / "jp.json")
    report.record("address.global_timer", G.Verdict("verified", value=0x1234),
                  at="2026-08-15T00:00:00Z")
    layout = working_layout("jp", report)
    assert layout.global_timer == 0x1234
    assert layout.mario_struct is None


def test_working_layout_never_overrides_a_shipped_value(tmp_path):
    report = Report(tmp_path / "us.json")
    report.record("address.global_timer", G.Verdict("verified", value=0xDEADBEEF),
                  at="2026-08-15T00:00:00Z")
    layout = working_layout("us", report)
    assert layout.global_timer == 0x8032D5D4   # the shipped value, untouched


# --- GateContext ---------------------------------------------------------

def test_gatecontext_raw_returns_the_underlying_memory(tmp_path):
    mem = _RomMem("us")
    ctx = GateContext("us", mem, Report(tmp_path / "us.json"),
                      prompt=_quiet, say=_quiet)
    assert ctx.raw() is mem


def test_gatecontext_candidate_falls_back_to_the_shipped_layout(tmp_path):
    report = Report(tmp_path / "us.json")
    ctx = GateContext("us", _RomMem("us"), report, prompt=_quiet, say=_quiet)
    assert ctx.candidate("global_timer") == 0x8032D5D4   # the shipped value


def test_gatecontext_candidate_prefers_a_verified_report_value_over_the_shipped_one(tmp_path):
    """A gate re-verifying its OWN field mid-run (or a two-phase gate whose
    later pass refines an earlier candidate, e.g. address.object_pool.confirm
    -- Task 9) must see the value THIS session just confirmed, not fall back
    to whatever shipped before it. layout.py itself is never touched here --
    only `working_layout`'s report-fills-None promise governs that."""
    report = Report(tmp_path / "us.json")
    report.record("address.global_timer", G.Verdict("verified", value=0x9999),
                  at="2026-08-15T00:00:00Z")
    ctx = GateContext("us", _RomMem("us"), report, prompt=_quiet, say=_quiet)
    assert ctx.candidate("global_timer") == 0x9999


def test_gatecontext_candidate_falls_back_to_the_symbol_map_for_jp(tmp_path):
    report = Report(tmp_path / "jp.json")
    ctx = GateContext("jp", _RomMem("jp"), report, prompt=_quiet, say=_quiet)
    # JP ships nothing for global_timer; the symbol map is the last resort.
    assert ctx.candidate("global_timer") is not None


def test_gatecontext_snapshots_raises_for_an_incomplete_jp_layout(tmp_path):
    report = Report(tmp_path / "jp.json")
    ctx = GateContext("jp", _RomMem("jp"), report, prompt=_quiet, say=_quiet)
    with pytest.raises(LayoutIncomplete):
        next(ctx.snapshots(0.1))


# --- summary() ---------------------------------------------------------

def test_summary_lists_promotions_and_failures(tmp_path, isolated_gates):
    G.register(
        _stub_gate("address.global_timer", value=0x1234),
        G.Gate("address.curr_level", "version", "address", "x", "x",
              lambda ctx: G.Verdict("failed", evidence="nope"), auto=True))
    report = run("jp", _RomMem("jp"), prompt=_quiet, say=_quiet,
                report_root=tmp_path)
    text = summary(report)
    assert "global_timer" in text and "0x00001234" in text
    assert "address.curr_level" in text and "nope" in text


# --- default_server() ---------------------------------------------------

def test_default_server_returns_a_url_or_none():
    result = default_server()
    assert result is None or result.startswith("http://127.0.0.1:")


class _NoRomMem(BufferMemory):
    def rom_header(self):
        return None


def test_an_unreadable_rom_header_warns_and_walks_the_named_version(tmp_path, isolated_gates):
    """`detected is None` is not a disagreement: which byte order PJ64 stores
    the ROM in is itself a live-gate item, so an unreadable header must not
    refuse the whole walk (review 2026-08-15)."""
    G.register(_stub_gate("address.global_timer"))
    said = []
    report = run("us", _NoRomMem(), prompt=_quiet, say=said.append, report_root=tmp_path)
    assert report.status("address.global_timer") == "verified"
    assert any("could not read the ROM header" in line for line in said)


def test_the_exit_code_counts_only_this_runs_failures(tmp_path, isolated_gates):
    """A stale failure loaded from a previous report must not fail today's
    `--only` of an unrelated gate."""
    from sm64_events.sync.runner import failed_this_run
    from sm64_events.sync.report import Report, report_path
    stale = Report(report_path("us", tmp_path))
    stale.record("address.curr_area", G.Verdict("failed", evidence="last week"),
                 "2026-08-01T00:00:00Z")
    G.register(_stub_gate("address.global_timer"), _stub_gate("address.curr_area"))
    report = run("us", _RomMem("us"), only="address.global_timer", prompt=_quiet,
                say=_quiet, report_root=tmp_path)
    assert report.status("address.curr_area") == "failed"      # still on disk
    assert failed_this_run(report) == []                         # not this run's


def test_the_summary_names_optional_gates_and_counts_required_ones(tmp_path, isolated_gates):
    from sm64_events.sync.runner import summary
    G.register(_stub_gate("address.global_timer"),
               G.Gate("cal.moment.display_lag", "landmarks", "calibration", "screenshot",
                      "needs a screenshot", lambda ctx: G.Verdict("skipped", evidence="screenshot"),
                      backs="sm64_events.detectors.moment.MomentDetector.DISPLAY_LAG_FRAMES",
                      auto=True, optional=True))
    report = run("us", _RomMem("us"), prompt=_quiet, say=_quiet, report_root=tmp_path)
    text = summary(report)
    assert "version: verified 1/1" in text
    assert "landmarks: verified 0/0" in text
    assert "optional gates not verified" in text and "cal.moment.display_lag (skipped)" in text

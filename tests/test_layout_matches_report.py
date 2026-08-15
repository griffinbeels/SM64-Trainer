"""The layout and the sync report cannot drift.

Promotion is human-in-the-loop by design (Claude reads `jp.json`, writes the
JP row with its evidence), so this is the guard that makes forgetting loud in
BOTH directions: a value the report verified that the layout still lacks, and
a shipped value the report says failed. A missing report file skips — the
runner has not been pointed at that version yet — and every field the report
does not mention is left alone.
"""
import pytest

from sm64_events.memory.layout import LAYOUT_ROWS, VERSIONS, layout_for
from sm64_events.sync.report import Report, report_path


def _report(version: str) -> Report | None:
    path = report_path(version)
    if not path.exists():
        return None
    return Report(path).load()


@pytest.mark.parametrize("version", VERSIONS)
def test_every_verified_address_is_shipped_and_no_shipped_address_is_refuted(version):
    report = _report(version)
    if report is None:
        pytest.skip(f"no sync report for {version} at {report_path(version)}")
    layout = layout_for(version)
    unshipped, refuted = [], []
    for row in LAYOUT_ROWS:
        verdict = report.verdicts.get(f"address.{row.field}")
        if verdict is None:
            continue
        shipped = layout.value(row.field)
        if verdict.status == "verified" and verdict.value is not None:
            if shipped is None:
                unshipped.append(f"{row.field} = {verdict.value:#010x}")
            elif shipped != verdict.value:
                refuted.append(f"{row.field}: layout {shipped:#010x}, "
                               f"report verified {verdict.value:#010x}")
        elif verdict.status == "failed" and shipped is not None:
            refuted.append(f"{row.field}: layout {shipped:#010x} but the "
                           f"report says failed ({verdict.evidence})")
    assert not unshipped, (
        f"{version}: the sync report verified these and layout.py still has "
        f"None -- promote them (with the report's evidence): {unshipped}")
    assert not refuted, f"{version}: layout and report disagree: {refuted}"

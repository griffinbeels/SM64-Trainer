# src/sm64_events/core/diagnostics.py
"""One-button debug report: capped tails of every log the app already keeps,
assembled into ONE markdown file an end user can drag into a bug report.

Every section is independently wrapped: a source that cannot be read yields a
section naming its own failure instead of an exception — the report must
generate WHEN things are broken, that being its entire purpose. Nothing here
imports FastAPI, so pytest drives the builder directly."""
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sm64_events.core.diagnostic_export import native_export, report_line, report_value
from sm64_events.core.diagnostic_privacy import safe_snapshot
from sm64_events.core.diagnostic_retention import REPORT_KEEP as KEEP_REPORTS, REPORT_MAX_BYTES, report_directory
from sm64_events.core.error_reporting_privacy import scrub_text
from sm64_events.core.native_diagnostics import bounded_tail

LOG_TAIL_LINES = 400
JOURNAL_TAIL = 100
UILOG_TAIL = 50
PERF_TAIL = 30
LINE_CLIP = 500      # one pathological line cannot eat the size budget

# Import time == server start (server/app.py imports this module when the
# process boots), so this is the process clock the header's uptime reads.
_STARTED_MONO = time.monotonic()

UILOG_EMPTY = ("empty — the page posts these itself, so silence means the "
               "open tab is running old JS, or no page was open")


def _tail(path: Path, count: int) -> list[str]:
    text, _ = bounded_tail(path, limit=256 * 1024)
    return [report_line(line)[:LINE_CLIP] for line in text.splitlines()[-count:]]


def _fence(lines: list[str]) -> str:
    return "```\n" + "\n".join(lines) + "\n```"


def _section(title: str, body_fn: Callable[[], str]) -> str:
    try:
        body = body_fn()
    except Exception as err:  # noqa: BLE001 - a dead source is a finding, never a crash.
        body = f"unavailable: {type(err).__name__}: {scrub_text(err)}"
    return f"## {title}\n\n{body}\n"


def _log_level(line: str) -> str:
    # logging_setup format: "<asctime>Z <LEVEL> <name> <message>"
    parts = line.split(maxsplit=2)
    return parts[1] if len(parts) > 1 else ""


def _problems(log_lines: list[str]) -> str:
    errors = [ln for ln in log_lines if _log_level(ln) == "ERROR"]
    warnings = [ln for ln in log_lines if _log_level(ln) == "WARNING"]
    if not errors and not warnings:
        return "no ERROR or WARNING lines in the log tail"
    head = (f"{len(errors)} ERROR / {len(warnings)} WARNING lines "
            f"in the last {LOG_TAIL_LINES} log lines. Newest errors:")
    return head + "\n\n" + _fence((errors or warnings)[-5:])


def build_report(*, version: str, frozen: bool, port: int, data_dir: str,
                 log_file: Path, ui_log_file: Path, perf_log_file: Path,
                 health: Callable[[], dict],
                 events: Callable[[], list], replay: Callable[[], dict] | None = None,
                 setup: Callable[[], dict] | None = None, build: dict | None = None,
                 snapshot: Callable[[], dict] | None = None) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = (
        "# SM64 Trainer debug report\n\n"
        f"Generated {now} (UTC). Private paths and display labels are omitted. "
        "This export leaves your machine only when you share it.\n\n"
        f"- version: {version}\n"
        f"- install: {'packaged exe' if frozen else 'from source (dev)'}\n"
        f"- port: {port}\n"
        f"- platform: {platform.platform()}\n"
        f"- uptime_s: {int(time.monotonic() - _STARTED_MONO)}\n"
        "- data dir: [path omitted]\n")
    try:
        captured = snapshot() if snapshot else {"available": False}
    except Exception as error:  # noqa: BLE001 - still export every other section.
        captured = {"available": False, "error_type": type(error).__name__}

    try:
        log_lines: list[str] | None = _tail(log_file, LOG_TAIL_LINES)
    except Exception:
        log_lines = None

    def _log_body() -> str:
        if log_lines is None:
            _tail(log_file, LOG_TAIL_LINES)  # re-raise the real reason
        return _fence(log_lines)

    def _journal_body() -> str:
        rows = events()[-JOURNAL_TAIL:]
        if not rows:
            return "no events recorded"
        return _fence([
            f"{r.wall_time_utc} f{r.frame} {r.type} "
            f"{json.dumps(report_value(r.payload))[:LINE_CLIP]}" for r in rows])

    def _uilog_body() -> str:
        if not ui_log_file.exists():
            return UILOG_EMPTY
        lines = _tail(ui_log_file, UILOG_TAIL)
        return _fence(lines) if lines else UILOG_EMPTY

    def _perf_body() -> str:
        if not perf_log_file.exists():
            return "no perf samples (perf monitoring disabled?)"
        return _fence(_tail(perf_log_file, PERF_TAIL))

    return "\n".join([
        head,
        _section("Health", lambda: "```json\n" + json.dumps(
            report_value(health()), indent=2, default=str) + "\n```"),
        _section("Replay and GPU", lambda: "```json\n" + json.dumps(
            safe_snapshot({"capture": replay()})["capture"] if replay else {"available": False}, indent=2, default=str) + "\n```"),
        _section("Last observed setup", lambda: "```json\n" + json.dumps(
            report_value(setup() if setup else None), indent=2, default=str) + "\n```"),
        _section("Trainer diagnostic snapshot", lambda: "```json\n" + json.dumps(
            {key: value for key, value in captured.items() if key != "native_log_tails"}, indent=2, default=str) + "\n```"),
        _section("Native plugin log records", lambda: "```json\n" + native_export(captured) + "\n```"),
        _section("Build", lambda: "```json\n" + json.dumps(build or {}, indent=2) + "\n```"),
        _section("Problems", lambda: _problems(log_lines)
                 if log_lines is not None else _log_body()),
        _section("Server log", _log_body),
        _section("Recent game events", _journal_body),
        _section("What the screen drew", _uilog_body),
        _section("Perf samples", _perf_body),
    ])


def write_report(report_dir: Path, markdown: str, *,
                 keep: int = KEEP_REPORTS) -> Path:
    """Write one timestamp-named report and prune to the newest `keep`."""
    keep = max(1, min(keep, KEEP_REPORTS))
    report_dir = report_directory(report_dir, keep=keep)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    out = report_dir / f"debug-report-{stamp}.md"
    raw = markdown.encode("utf-8")
    suffix = b"\n\n[Report truncated at the 1 MiB size limit.]\n"
    if len(raw) > REPORT_MAX_BYTES:
        raw = raw[:REPORT_MAX_BYTES - len(suffix)].decode("utf-8", "ignore").encode("utf-8") + suffix
    out.write_bytes(raw)
    # Resolved: the path is shown to the user and handed to the reveal
    # endpoint, and from source data_root() is cwd-relative.
    return out.resolve()

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

LOG_TAIL_LINES = 400
JOURNAL_TAIL = 100
UILOG_TAIL = 50
PERF_TAIL = 30
LINE_CLIP = 500      # one pathological line cannot eat the size budget
KEEP_REPORTS = 5

# Import time == server start (server/app.py imports this module when the
# process boots), so this is the process clock the header's uptime reads.
_STARTED_MONO = time.monotonic()

UILOG_EMPTY = ("empty — the page posts these itself, so silence means the "
               "open tab is running old JS, or no page was open")


def _tail(path: Path, count: int) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [line[:LINE_CLIP] for line in lines[-count:]]


def _fence(lines: list[str]) -> str:
    return "```\n" + "\n".join(lines) + "\n```"


def _section(title: str, body_fn: Callable[[], str]) -> str:
    try:
        body = body_fn()
    except Exception as err:  # a dead source is a finding, never a crash
        body = f"unavailable: {err!r}"
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
                 events: Callable[[], list]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = (
        "# SM64 Trainer debug report\n\n"
        f"Generated {now} (UTC). Paths in this file include your Windows "
        "username; it leaves your machine only when you share it.\n\n"
        f"- version: {version}\n"
        f"- install: {'packaged exe' if frozen else 'from source (dev)'}\n"
        f"- port: {port}\n"
        f"- platform: {platform.platform()}\n"
        f"- uptime_s: {int(time.monotonic() - _STARTED_MONO)}\n"
        f"- data dir: {data_dir}\n")

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
            f"{json.dumps(r.payload)[:LINE_CLIP]}" for r in rows])

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
            health(), indent=2, default=str) + "\n```"),
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
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    out = report_dir / f"debug-report-{stamp}.md"
    out.write_text(markdown, encoding="utf-8", newline="")
    for stale in sorted(report_dir.glob("debug-report-*.md"))[:-keep]:
        stale.unlink()
    # Resolved: the path is shown to the user and handed to the reveal
    # endpoint, and from source data_root() is cwd-relative.
    return out.resolve()

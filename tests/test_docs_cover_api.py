# tests/test_docs_cover_api.py
"""Every `/api` route must appear in the consumer-facing docs, AND -- for the
one shape of endpoint where this is mechanically checkable -- the documented
RESPONSE must match the real one.

WHY test_every_api_route_is_documented is a test and not a review checklist:
the REST surface is documented in TWO files — `docs/api.md` (session/segments/
routes/runs/replay/compare) and `README.md` (the ranks tables) — and a
session that checks only one ships a stale contract. That exact miss cost a
whole final-review round on 2026-07-23 (the ranks `?purge=true` + `seeded`
additions were written up in docs/api.md's neighbourhood while the real
rows lived in README), and the same sweep found the entire `/api/run/*`
and route CRUD surface had never been documented at all.

Adding a route now fails here until it is documented in either file.
Path parameters are normalised, so `{route_id}` in code may be written
`{id}` in prose — only the path SHAPE has to match.

WHY test_dataclass_backed_response_shapes_match_docs exists (2026-07-28):
adding `arms` to `BacktestReport` (tracking/backtest.py) shipped as an API
change with NO code change at the API layer — `server/api.py`'s backtest
handler is `return dataclasses.asdict(report)`, no allowlist, so any field on
the dataclass is serialized automatically. `docs/api.md` documented a 7-key
response that was quietly 8 keys until a reviewer read the handler; nothing
here would have caught it, because the route-existence test above only checks
that a PATH string appears in prose, never that a documented response SHAPE
matches the real one.

This deliberately does NOT try to generalise to every endpoint: most build
their response dict inline (server/api.py) and the docs describe them in free
prose that no regex can reliably parse — a guard that matched nothing there
would stay green forever while reading as protection, which is worse than no
guard (see `.claude/rules/tracking-storage.md`'s single-source-of-truth rule
on scans that match nothing). The only case this covers is a response
generated WHOLESALE by `dataclasses.asdict` of a known dataclass, documented
as a `Response: {a, b, c}` brace-list on that route's own docs/api.md row.
`BacktestReport` is the only one today (the only other `dataclasses.asdict`
call site would need its own row in DATACLASS_BACKED_RESPONSES below, added
only when a new one exists AND its docs use the same brace-list shape)."""
import dataclasses
import re
from pathlib import Path

import pytest

from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.backtest import BacktestReport
from sm64_events.tracking.service import TrackerService

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_FILES = ("README.md", "docs/api.md")


class OfflineMemory:
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


def _normalise(text: str) -> str:
    """Collapse every {param} to {} so docs may name parameters freely."""
    return re.sub(r"\{[^}]+\}", "{}", text)


def test_every_api_route_is_documented(tmp_path):
    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    service = TrackerService(db, broadcaster)
    app = create_app(Poller(OfflineMemory(), [], service), broadcaster,
                     service=service)
    documented = _normalise(
        "\n".join((REPO_ROOT / name).read_text(encoding="utf-8")
                  for name in DOC_FILES))
    routes = sorted({r.path for r in app.routes
                     if getattr(r, "path", "").startswith("/api")})
    undocumented = [p for p in routes if _normalise(p) not in documented]
    assert not undocumented, (
        "undocumented API routes (add them to docs/api.md, or to README.md "
        f"if they belong to a README-documented family like ranks): {undocumented}")


# -- response-shape guard (dataclasses.asdict endpoints only) ---------------
# route label (exactly as it appears backtick-quoted in docs/api.md) -> the
# dataclass server/api.py hands to dataclasses.asdict() for that response.
DATACLASS_BACKED_RESPONSES = {
    "POST /api/segments/backtest": BacktestReport,
}


def _documented_response_fields(doc_text: str, route_label: str) -> set[str]:
    """Pull the `Response: {a, b, c}` brace-list off a route's own docs/api.md
    row. Each row is one (long) line, so finding the line naming the route and
    searching within it is enough -- no markdown table parsing required."""
    line = next((l for l in doc_text.splitlines()
                if f"`{route_label}`" in l), None)
    assert line is not None, (
        f"no docs/api.md row found for `{route_label}` -- has the route's "
        "docs entry moved or been reworded?")
    match = re.search(r"Response:\s*`\{([^}]*)\}`", line)
    assert match is not None, (
        f"`{route_label}`'s docs/api.md row has no `Response: {{...}}` "
        "brace-list for this test to parse -- restore the literal brace-list "
        "(prose may still explain each field around it)")
    return {name.strip() for name in match.group(1).split(",")}


@pytest.mark.parametrize("route_label,report_type",
                          DATACLASS_BACKED_RESPONSES.items())
def test_dataclass_backed_response_shapes_match_docs(route_label, report_type):
    doc_text = (REPO_ROOT / "docs/api.md").read_text(encoding="utf-8")
    documented = _documented_response_fields(doc_text, route_label)
    real = {f.name for f in dataclasses.fields(report_type)}
    assert documented == real, (
        f"{route_label}'s docs/api.md Response brace-list has drifted from "
        f"{report_type.__name__}'s real fields — documented-only: "
        f"{documented - real or '(none)'}, real-only: {real - documented or '(none)'}")

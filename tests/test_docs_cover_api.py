# tests/test_docs_cover_api.py
"""Every `/api` route must appear in the consumer-facing docs.

WHY this is a test and not a review checklist: the REST surface is
documented in TWO files — `docs/api.md` (session/segments/routes/runs/
replay/compare) and `README.md` (the ranks tables) — and a session that
checks only one ships a stale contract. That exact miss cost a whole
final-review round on 2026-07-23 (the ranks `?purge=true` + `seeded`
additions were written up in docs/api.md's neighbourhood while the real
rows lived in README), and the same sweep found the entire `/api/run/*`
and route CRUD surface had never been documented at all.

Adding a route now fails here until it is documented in either file.
Path parameters are normalised, so `{route_id}` in code may be written
`{id}` in prose — only the path SHAPE has to match."""
import re
from pathlib import Path

from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
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

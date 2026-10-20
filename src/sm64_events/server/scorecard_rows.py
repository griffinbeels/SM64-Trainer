"""One scope-to-row specification for local and published Scorecards."""
from fastapi import HTTPException

from sm64_events.ranks.scorecard import rows_for_course, rows_for_route, template_rows
from sm64_events.tracking.views import segment_courses


def scope_rows(service, scope_id: str) -> list[dict]:
    """Resolve the viewer's scope; a missing route remains a deliberate 404."""
    if service.db is None:
        raise HTTPException(503, "scorecard unavailable")
    kind, _, rest = scope_id.partition(":")
    if kind == "course" and rest.isdigit():
        try:
            return rows_for_course(int(rest))
        except LookupError:
            raise HTTPException(404, f"no scorecard for {scope_id!r}") from None
    definitions = service.db.segment_defs()
    fights = [row for row in definitions if row.get("category") == "Bowser Fights"]
    if scope_id == "overall":
        return template_rows(fight_segments=[(f"segment:{row['id']}", row["name"], row.get("seed_key")) for row in fights])
    if kind == "route" and rest.isdigit():
        route = next((row for row in service.db.routes() if row["id"] == int(rest)), None)
        if route is not None:
            return rows_for_route(route, segment_labels={row["id"]: row["name"] for row in definitions},
                                  segment_courses=segment_courses(service.db),
                                  fight_segment_ids={row["id"]: row.get("seed_key") for row in fights})
    raise HTTPException(404, f"unknown scope {scope_id!r}")

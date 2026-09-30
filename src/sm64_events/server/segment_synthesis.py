"""Assemble a recorder draft from its path and independent start samples.

The representative path keeps the recorder's chronological contract: first
starts, last finishes, selected middles are ordered waypoints. Extra start
samples may come from another attempt anywhere in the journal. They change
only the OR start clauses, never the demonstrated walk or suggested name.
"""
from collections.abc import Iterable

from fastapi import HTTPException

from sm64_events.storage.db import EventRow
from sm64_events.tracking.segments import clause_sentence
from sm64_events.tracking.synthesize import (clause_for, suggest_name,
                                             walked_steps)


def _parse_ids(value: str, field: str) -> list[int]:
    try:
        return sorted({int(part) for part in value.split(",") if part})
    except ValueError:
        raise HTTPException(422, f"{field} must be a comma-separated list of "
                                 "timeline row ids") from None


def _picked_rows(rows_by_id: dict[int, EventRow], ids: list[int]) -> list[EventRow]:
    if any(row_id not in rows_by_id for row_id in ids):
        raise HTTPException(404, "unknown timeline event id")
    return [rows_by_id[row_id] for row_id in ids]


def _clause(row: EventRow, role: str) -> dict:
    # A waypoint uses the end role: it names what the player REACHES.
    clause = clause_for(row, "end" if role == "step" else role)
    if clause is None:
        raise HTTPException(409,
            f"That moment can't be this segment's {role} — it doesn't "
            "carry enough information to define a trigger from (for "
            "example, a reset with no recorded place).")
    return clause


def _start_clauses(rows: list[EventRow]) -> list[dict]:
    # Row ids distinguish demonstrations; equivalent demonstrations need
    # only one condition. Primary first also preserves the stored origin.
    clauses = []
    for row in rows:
        clause = _clause(row, "start")
        if clause not in clauses:
            clauses.append(clause)
    return clauses


def synthesize_recording(rows: Iterable[EventRow], ids: str,
                         start_ids: str | None, names: dict) -> dict:
    """Read-only draft; retain singular fields for existing recorder clients.

    ``picked_explicit`` remembers whether the representative selection had
    any middles, even if every middle was promoted to an alternative start.
    An empty filtered ``picked`` must not revive automatically walked steps.
    """
    picked_ids = _parse_ids(ids, "ids")
    if len(picked_ids) < 2:
        raise HTTPException(422, "pick at least two moments — one to start "
                                 "on and one to finish on")
    extra_ids = _parse_ids(start_ids or "", "start_ids")
    rows_by_id = {row.id: row for row in rows}
    picked_rows = _picked_rows(rows_by_id, picked_ids)
    extra_rows = _picked_rows(rows_by_id, extra_ids)
    start_row, end_row = picked_rows[0], picked_rows[-1]
    if end_row.id in extra_ids:
        raise HTTPException(422, "the finish moment can't also be an "
                                 "alternative start")
    start_rows = [start_row, *(row for row in extra_rows
                               if row.id != start_row.id)]
    starts = _start_clauses(start_rows)
    end_clause = _clause(end_row, "end")
    start_sentences = [clause_sentence(clause, names) for clause in starts]
    steps = [{**step, "sentence": clause_sentence(step["clause"], names)}
             for step in walked_steps(rows_by_id.values(), start_row, end_row)]
    middle_rows = picked_rows[1:-1]
    extra_set = set(extra_ids)
    picked = []
    for row in middle_rows:
        if row.id in extra_set:
            continue
        clause = _clause(row, "step")
        picked.append({"id": row.id, "clause": clause,
                       "sentence": clause_sentence(clause, names)})
    return {"start_clause": starts[0], "end_clause": end_clause,
            "start_sentence": start_sentences[0],
            "start_clauses": starts, "start_sentences": start_sentences,
            "end_sentence": clause_sentence(end_clause, names),
            "steps": steps, "picked": picked,
            "picked_explicit": bool(middle_rows),
            "name": suggest_name(starts[0], end_clause, names)}

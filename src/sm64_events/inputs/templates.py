# src/sm64_events/inputs/templates.py
"""Template tracks — the input a run is compared against.

**A template is a DOCUMENT, not a flag on an attempt** (his ruling,
2026-08-20). That is what lets one come from an attempt he marked, a file
another player sent him, or one he typed out by hand; `origin` records which,
and nothing downstream cares.

Storing the document TEXT rather than a row per frame is the same decision.
What he exports and what the timeline draws are then the same bytes, so a
round trip cannot quietly change what he is comparing against.

One template is ACTIVE per (kind, entity, strategy). The partial unique index
in migration v28 makes that a fact rather than a convention; marking a new one
stands the old one down in the same transaction.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from sm64_events.inputs.document import DocumentError, decode


@dataclass(frozen=True)
class Template:
    id: int
    kind: str            # star | segment
    entity_key: str
    strat_tag: str | None
    name: str
    origin: str          # attempt:<id> | import:<name> | authored
    document: str
    active: bool
    created_utc: str

    def frames(self):
        """The track itself. Raises DocumentError on a document that stopped
        being loadable — which is possible, because a person may have edited
        it by hand since it was stored."""
        return decode(self.document).frames

    def summary(self) -> dict:
        """Library metadata comes from the same portable document we export."""
        result = {"id": self.id, "kind": self.kind, "entity_key": self.entity_key,
                  "strat_tag": self.strat_tag, "name": self.name, "origin": self.origin,
                  "active": self.active, "created_utc": self.created_utc,
                  "author": None, "frames": 0}
        try:
            document = decode(self.document)
            result.update(author=document.author, frames=document.frame_count)
        except DocumentError as error:
            result["error"] = str(error)
        return result


def _row(row) -> Template:
    return Template(id=row["id"], kind=row["kind"],
                    entity_key=row["entity_key"], strat_tag=row["strat_tag"],
                    name=row["name"], origin=row["origin"],
                    document=row["document"], active=bool(row["active"]),
                    created_utc=row["created_utc"])


class TemplateStore:
    def __init__(self, conn, lock):
        self._conn = conn
        self._lock = lock

    def save(self, *, kind: str, entity_key: str, strat_tag: str | None,
             name: str, origin: str, document: str,
             active: bool = True) -> Template:
        """Store a document, refusing one that will not load.

        Validation happens HERE rather than at read time on purpose: a
        template that cannot be decoded is useless, and finding that out when
        he opens a drawer is finding out at the worst possible moment.
        """
        if kind not in ("star", "segment"):
            raise ValueError("template kind must be star or segment")
        pattern = r"[0-9]+-[0-9]+" if kind == "star" else r"[0-9]+"
        if not re.fullmatch(pattern, entity_key):
            raise ValueError(f"invalid {kind} template entity key")
        if not name.strip() or len(name) > 200:
            raise ValueError("template name must contain 1 to 200 characters")
        if not decode(document).frames:
            raise DocumentError("this document has no captured input")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if active:
                self._conn.execute(
                    "UPDATE input_templates SET active=0"
                    " WHERE kind=? AND entity_key=? AND IFNULL(strat_tag,'')=?",
                    (kind, entity_key, strat_tag or ""))
            cursor = self._conn.execute(
                "INSERT INTO input_templates (kind, entity_key, strat_tag,"
                " name, origin, document, active, created_utc)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (kind, entity_key, strat_tag, name, origin, document,
                 1 if active else 0, now))
            self._conn.commit()
            new_id = cursor.lastrowid
        return self.get(new_id)

    def get(self, template_id: int) -> Template:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM input_templates WHERE id=?",
                (template_id,)).fetchone()
        if row is None:
            raise LookupError(f"no input template {template_id}")
        return _row(row)

    def active_for(self, kind: str, entity_key: str,
                   strat_tag: str | None) -> Template | None:
        """The template this run is compared against, or None.

        Falls back to the strategy-less template when the strategy has none of
        its own: a template recorded before he named a strategy still describes
        the same movement, and refusing to show it would hide real history
        behind a label.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM input_templates"
                " WHERE kind=? AND entity_key=? AND active=1"
                " AND IFNULL(strat_tag,'')=? LIMIT 1",
                (kind, entity_key, strat_tag or "")).fetchone()
            if row is None and strat_tag:
                row = self._conn.execute(
                    "SELECT * FROM input_templates"
                    " WHERE kind=? AND entity_key=? AND active=1"
                    " AND IFNULL(strat_tag,'')='' LIMIT 1",
                    (kind, entity_key)).fetchone()
        return _row(row) if row is not None else None

    def list_for(self, kind: str, entity_key: str) -> list[Template]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM input_templates WHERE kind=? AND entity_key=?"
                " ORDER BY created_utc DESC", (kind, entity_key)).fetchall()
        return [_row(row) for row in rows]

    def all(self) -> list[Template]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM input_templates ORDER BY created_utc DESC"
            ).fetchall()
        return [_row(row) for row in rows]

    def activate(self, template_id: int) -> Template:
        template = self.get(template_id)
        with self._lock:
            self._conn.execute(
                "UPDATE input_templates SET active=0"
                " WHERE kind=? AND entity_key=? AND IFNULL(strat_tag,'')=?",
                (template.kind, template.entity_key, template.strat_tag or ""))
            self._conn.execute("UPDATE input_templates SET active=1 WHERE id=?",
                               (template_id,))
            self._conn.commit()
        return self.get(template_id)

    def delete(self, template_id: int) -> None:
        """Erase it. His standing ruling on deletion (2026-08-02): marking a
        row removed is worthless — "just completely erase them"."""
        self.get(template_id)
        with self._lock:
            self._conn.execute("DELETE FROM input_templates WHERE id=?",
                               (template_id,))
            self._conn.commit()


__all__ = ["DocumentError", "Template", "TemplateStore"]

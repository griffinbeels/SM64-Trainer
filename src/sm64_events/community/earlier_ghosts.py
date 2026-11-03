"""Ghosts this install recorded that its account may never have shared.

His decision, 2026-11-03: ghosts earned before sign-in upload once someone signs
in and shares, "and then those should get associated with his profile." Only the
player's answer starts it: the published terms promise that signup enables
*future* uploads, so a login alone still adopts nothing (`community/outbox.py`).

The answer is one small file beside the outbox. Once an account says yes, no
other account can ever share this install's earlier runs, because the rows do not
record who was signed in when they were recorded.
"""
import json
from pathlib import Path
from uuid import UUID

from .config import CommunityError
from .outbox import _read_document, _replace, checked_source

ANSWER_NAME = "earlier-ghosts.json"
SHARED, STOPPED, NOT_NOW = "shared", "stopped", "not_now"


def own_sources(rows, player_id, clips_root):
    """Offers for the rows this install recorded itself, skipping anything else.

    Only the recorder's own identity with a local lineage qualifies, so an
    imported or bundled ghost can never go up under this account. The upload
    re-reads the bytes and refuses a clip its bytes do not bear out.
    """
    sources = []
    for row in rows:
        if (row.get("player_id") != player_id or row.get("simulated") is True or row.get("certified") is not True
                or row.get("valid") is not True or row.get("full_start") is not True
                or not isinstance(row.get("source"), dict) or not row.get("strategy")):
            continue
        path = Path(clips_root) / "clips" / f"{row['sha256']}.ghost"
        if not path.is_file() or path.is_symlink():
            continue
        sources.append({"target": row["target"], "clock": row["clock"], "game_version": row["game_version"],
                        "strategy": row["strategy"], "frames": row["frames"], "path": str(path),
                        "raw_sha256": row["sha256"], "full_start": True, "valid": True})
    return tuple(sources)


def checked_offers(sources, freeze, *, foreign):
    """The frozen offers to remember, leaving out bytes another account holds."""
    offers = []
    for source in sources:
        try:
            frozen = freeze(source)
            if frozen is not None and frozen["raw_sha256"] not in foreign:
                offers.append(checked_source({key: value for key, value in frozen.items() if key != "consent_epoch"}))
        except CommunityError:
            continue
    return offers


class EarlierAnswer:
    """Who answered, and how. An unreadable file reads as no answer yet."""
    def __init__(self, root):
        self.path = Path(root) / ANSWER_NAME

    def read(self):
        try:
            rows = _read_document(self.path, "answers", 1)
            owner, answer = rows[0]["owner"], rows[0]["answer"]
            if str(UUID(owner)) != owner or answer not in (SHARED, STOPPED, NOT_NOW):
                raise ValueError()
        except (OSError, ValueError, UnicodeError, RecursionError, LookupError, TypeError, AttributeError):
            return None
        return {"owner": owner, "answer": answer}

    def write(self, owner, answer):
        _replace(self.path, json.dumps({"v": 1, "answers": [{"owner": owner, "answer": answer}]}).encode())

    def closed_to(self, owner):
        """Whether `owner` may no longer share: done already, or another account's."""
        record = self.read()
        return record is not None and (
            record["answer"] == SHARED or record["owner"] != owner and record["answer"] == STOPPED)

    def asks(self, owner):
        """Whether `owner` has yet to answer at all."""
        record = self.read()
        return record is None or record["owner"] != owner and record["answer"] == NOT_NOW

    def stop(self, owner):
        """Sign-out or sharing off cancelled the uploads. Nothing restarts by itself:
        only the Settings button can share again, and only for this owner."""
        record = self.read()
        if record is not None and record["owner"] == owner and record["answer"] == SHARED:
            self.write(owner, STOPPED)

    def stop_everyone(self):
        record = self.read()
        if record is not None and record["answer"] == SHARED:
            self.write(record["owner"], STOPPED)

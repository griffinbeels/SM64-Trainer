"""Ghosts this install recorded that its account may never have shared.

His decision, 2026-11-03: "No question. Just upload automatically", and "those
should get associated with his profile." The first account that shares from this
install claims them. Terms `beta-2` say so, so an account that last accepted older
terms adopts nothing until it accepts the new ones (`OLDER_TERMS`).

The claim is one small file beside the outbox. No other account can ever share
this install's earlier runs, because the rows do not record who was signed in when
they were recorded. Removing shared ghosts or deleting the account closes the claim
for good, so that those ghosts never come back by themselves.
"""
import json
from pathlib import Path
from uuid import UUID

from .config import CommunityError
from .outbox import _read_document, _replace, checked_source

CLAIM_NAME = "earlier-ghosts.json"
QUEUED, STOPPED, REMOVED = "queued", "stopped", "removed"
OLDER_TERMS = frozenset({"beta-1"})
"""Terms that promised only future uploads. Accepting these never shares older ghosts."""


def own_sources(rows, player_id, clips_root, *, still_good):
    """Offers for the rows this install recorded itself, skipping anything else.

    Only the recorder's own identity with a local lineage qualifies, so an
    imported or bundled ghost can never go up under this account. `still_good`
    asks whether today's rules still admit the run behind a row: the row's own
    marks only say what was true when it was saved. The upload re-reads the
    bytes and refuses a clip its bytes do not bear out.
    """
    sources = []
    for row in rows:
        if (row.get("player_id") != player_id or row.get("simulated") is True or row.get("certified") is not True
                or row.get("valid") is not True or row.get("full_start") is not True
                or not isinstance(row.get("source"), dict) or not row.get("strategy") or not still_good(row)):
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


class EarlierClaim:
    """Which account holds this install's earlier ghosts. An unreadable file reads as none yet."""
    def __init__(self, root):
        self.path = Path(root) / CLAIM_NAME

    def read(self):
        try:
            rows = _read_document(self.path, "claims", 1)
            owner, state = rows[0]["owner"], rows[0]["state"]
            if str(UUID(owner)) != owner or state not in (QUEUED, STOPPED, REMOVED):
                raise ValueError()
        except (OSError, ValueError, UnicodeError, RecursionError, LookupError, TypeError, AttributeError):
            return None
        return {"owner": owner, "state": state}

    def write(self, owner, state):
        _replace(self.path, json.dumps({"v": 1, "claims": [{"owner": owner, "state": state}]}).encode())

    def open_to(self, owner):
        """Whether `owner` should queue them now: nobody claimed them, or its own uploads were cancelled."""
        record = self.read()
        return record is None or record == {"owner": owner, "state": STOPPED}

    def stop(self, owner):
        """Sign-out or sharing off cancelled the uploads. The same account resumes when it shares again."""
        record = self.read()
        if record is not None and record["owner"] == owner and record["state"] == QUEUED:
            self.write(owner, STOPPED)

    def stop_everyone(self):
        record = self.read()
        if record is not None and record["state"] == QUEUED:
            self.write(record["owner"], STOPPED)

    def remove(self, owner):
        """The player removed their shared ghosts. These never go up by themselves again."""
        self.write(owner, REMOVED)

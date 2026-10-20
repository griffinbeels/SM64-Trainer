"""Offline cloud boundary around real Library routes and the real saved-rival owner."""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import threading
from types import SimpleNamespace

from sm64_events.community.config import CommunityError
from sm64_events.ghost.rivals import Rivals, wire
from sm64_events.ghost.session import GhostSession
from sm64_events.ghost.sharing_targets import describe
from sm64_events.server.ghost_api import no_session_state
from tests.test_ghost_adaptive import recording
from tests.test_ghost_api import FakeSession
from tests.test_ghost_rivals import Native, Population

ACTIVE = {"kind":"star","entity_key":"2-1","strat":"Standard"}
TARGET = {"kind":"star","entity_key":"2-4","strat":"TJ Owlless"}
LONG_PLAYER = "René the Patient Speedrunner"
UID = "11111111-1111-4111-8111-111111111111"


class Community:
    def __init__(self, rows):
        self.rows,self.calls,self.mode = rows,[],"online"
        self.state = {"uid":UID,"authenticated":True,"enabled":True,"status":"active"}
        self.account = SimpleNamespace(snapshot=lambda:dict(self.state))

    def refused_assets(self):
        return frozenset()

    def _check(self):
        if self.mode == "offline":
            raise CommunityError("offline","Shared ghosts could not be reached.",status=503)

    async def library_summary(self, cursor, *, target=None, game_version=None):
        self.calls.append(("summary",cursor,deepcopy(target),game_version))
        self._check()
        rows = [] if self.mode=="empty" else self.rows
        groups = {}
        for row in rows:
            if (target is not None and row["target"]["entity_key"]!=target["entity_key"]
                    or game_version is not None and row["game_version"]!=game_version):
                continue
            key = row["target"]["entity_key"],row["strategy"],row["game_version"]
            groups.setdefault(key,[]).append(row)
        entries = []
        for group in groups.values():
            row = group[0]
            entries.append({key:deepcopy(row[key]) for key in ("target","clock","game_version","strategy")}
                           | {"recordings":len(group),"players":len({item["player_id"] for item in group}),
                              "best_frames":min(item["frames"] for item in group)})
        return {"generation":"ui-fixture","entries":entries,"next_cursor":None,"complete":True}

    async def library_recordings(self, target, clock, version, strategy, cursor):
        self.calls.append(("recordings",deepcopy(target),clock,version,strategy,cursor))
        self._check()
        rows = [wire(row) for row in self.rows if row["target"]["entity_key"]==target["entity_key"]
                and (row["clock"],row["game_version"],row["strategy"])==(clock,version,strategy)]
        return {"generation":"ui-fixture","recordings":rows,"next_cursor":None,"complete":True}

    async def recording_metadata(self, identifier):
        self.calls.append(("metadata",identifier))
        self._check()
        return wire(next(row for row in self.rows if row["id"]==identifier))


class LibrarySession(FakeSession):
    _library_owner = GhostSession._library_owner
    _library_work = GhostSession._library_work
    library_ghost_summary = GhostSession.library_ghost_summary
    library_ghost_recordings = GhostSession.library_ghost_recordings
    save_library_rival = GhostSession.save_library_rival

    def __init__(self, tracker):
        super().__init__()
        self.rows = []
        for index in range(5):
            self.rows.append(recording(f"{90+index:064x}",entity_key="2-4",strategy=TARGET["strat"],
                frames=600+index*30,player_name=LONG_PLAYER if index==0 else f"Community Runner {index}",
                player_id=f"22222222-2222-4222-8222-{index+1:012d}",
                certified=False,structurally_valid=True,provenance="community-unverified",
                target=describe("star","2-4",TARGET["strat"],())))
        self.rows.append(recording("f"*64,entity_key="2-6",strategy="Community-only method",
            frames=2400,player_name="Ghost-only runner",certified=False,structurally_valid=True,
            provenance="community-unverified",target=describe("star","2-6","Community-only method",())))
        self._community = Community(self.rows)
        self.native,self.population = Native(),Population()
        self.population.rows = self.rows
        self._tracker = tracker
        self._rivals = Rivals(tracker,self.population,self.native,lambda:"us",community=self._community)
        self._rivals._load()
        self._rivals.target = dict(ACTIVE)
        self._rivals.identity = self._rivals.identity_for(ACTIVE)
        self._target = dict(ACTIVE)
        self._lock,self._lifecycle_lock = asyncio.Lock(),threading.RLock()
        self._closing = False
        self.requests = []

    async def start(self):
        return None

    async def stop(self):
        return None

    async def refresh(self, *, resolve=True):
        self.refreshes.append(resolve)
        return no_session_state()


@dataclass
class LibraryBrowser:
    page: object
    session: LibrarySession
    service: object
    base: str
    log_path: Path

    def practice_snapshot(self):
        service,rivals,native = self.service,self.session._rivals,self.session.native
        return (service.target,service.strat_tag,len(service.db.events()),len(service.db.attempts()),
                rivals.target,rivals.identity,rivals.rows,rivals.generation,rivals.enabled,
                deepcopy(rivals.options),len(native.preparations),native.canceled)

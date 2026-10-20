"""Real ProgressSync/app against an offline, revision-fenced v2 account boundary."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sm64_events.community import CommunityError
from sm64_events.community.progress_snapshot import checked_snapshot, project
from sm64_events.community.progress_state import same_publisher
from sm64_events.ghost.sharing_targets import canonical
from tests.community_library_fixture import LibrarySession
from tests.test_community_sync import recording

ROOT = Path(__file__).resolve().parents[1]
WIRE = json.loads((ROOT/"cloud/community/test/player-wire-fixture.json").read_text(encoding="utf-8"))
UID = WIRE["snapshot"]["uid"]
INSTALLATION = WIRE["enable"]["publisher"]["installation_id"]
PROFILE = WIRE["enable"]["publisher"]["profile_id"]
RIVAL = "40000000-0000-4000-8000-000000000001"
LONG_PLAYER = "RenéPatientRunner123"
SAVED = "2026-10-19T12:00:05Z"
OWN_RECORDING = "b" * 64


class Account:
    def __init__(self, *, virgin=False):
        self._epoch = 0
        self.calls,self.writes,self.receipts = [],[],{}
        self.offline,self.partial,self.private,self.unsupported = False,False,False,False
        self.state = {"uid":UID,"name":"My claimed account","authenticated":True,"stored":True,
            "enabled":True,"status":"active","sharing":True,"provider":"discord",
            "terms_url":"https://example.test/terms","privacy_url":"https://example.test/privacy",
            "terms_version":"terms-1","privacy_version":"privacy-1",
            "terms_version_current":"terms-1","privacy_version_current":"privacy-1"}
        # Most scenarios begin with an explicit previous opt-out. Only the
        # default-sharing case starts with genuinely unchosen cloud controls.
        revision = 0 if virgin else 1
        self.publication = {"v":2,"uid":UID,"revision":revision,"consent_revision":revision,"enabled":False,
            "visible":False,"publisher":None,"notice_version":"progress-1","content_hash":None,
            "captured_utc":None,"synced_utc":None,"limits":{"rows":4096,"bytes":1048576}}
        self.profile = {"v":2,"uid":UID,"revision":0,"bio":"","socials":[]}
        self.snapshots,self.metadata = {},[]

    def snapshot(self):
        return dict(self.state)

    async def restore(self):
        return self.snapshot()

    async def close(self):
        return None

    async def refresh(self):
        return self.snapshot()

    def seed(self, service, session):
        content = project(service)
        rival = deepcopy(content)
        next(row for row in rival["rows"] if row["target"]["entity_key"]=="2-4")["frames"] = 330
        self.snapshots[RIVAL] = {"revision":10,"content":rival,"captured_utc":SAVED,"synced_utc":SAVED}
        row = recording("a"*64,frames=600)
        row.update(player_id=RIVAL,player_name=LONG_PLAYER,strategy="TJ Owlless",
                   target={"kind":"star","entity_key":"2-4","name":"Whomp's Fortress · Fall onto the Caged Island","strat":"TJ Owlless"})
        self.metadata = [row]
        session.rows[0].update(row)
        own = recording(OWN_RECORDING,frames=420)
        own.update(player_id=UID,player_name=self.state["name"],strategy=row["strategy"],target=deepcopy(row["target"]))
        self.metadata.append(own)
        session.rows.append({**deepcopy(session.rows[0]),**own})
        session._community.account = self

    def retain_owner_snapshot(self, service):
        """An existing hidden snapshot remains removable after access lapses."""
        content = project(service)
        self.control({**deepcopy(WIRE["enable"]),"expected_revision":self.publication["revision"]})
        self.save_snapshot({**deepcopy(WIRE["snapshot"]),"content":content,
            "expected_revision":self.publication["revision"],"consent_revision":self.publication["consent_revision"],
            "content_hash":hashlib.sha256(canonical(content)).hexdigest()})
        self.publication["visible"] = False

    def _check_revision(self, body, current):
        if body["expected_revision"] != current["revision"]:
            raise CommunityError("revision_conflict","Publication changed. Review the current publisher.",status=409)

    def control(self, body):
        self._check_revision(body,self.publication)
        action = body["action"]
        enabling = action in ("enable","switch")
        if enabling and body["notice_version"]!="progress-1":
            raise CommunityError("progress_consent_required",status=403)
        if action=="enable" and self.publication["publisher"] and not same_publisher(self.publication["publisher"],body["publisher"]):
            raise CommunityError("publisher_conflict",status=409)
        self.publication.update(revision=self.publication["revision"]+1,
            consent_revision=self.publication["consent_revision"]+1,enabled=enabling)
        if enabling:
            self.publication["publisher"] = deepcopy(body["publisher"])
        if action in ("switch","withdraw"):
            self.snapshots.pop(UID,None)
            self.publication.update(visible=False,content_hash=None,captured_utc=None,synced_utc=None)
        return deepcopy(self.publication)

    def save_snapshot(self, body):
        self._check_revision(body,self.publication)
        if (not self.publication["enabled"] or body["consent_revision"]!=self.publication["consent_revision"]
                or not same_publisher(body["publisher"],self.publication["publisher"])):
            raise CommunityError("publisher_conflict",status=409)
        content = checked_snapshot(body["content"])
        assert hashlib.sha256(canonical(content)).hexdigest()==body["content_hash"]
        if body["content_hash"]!=self.publication["content_hash"]:
            self.publication["revision"] += 1
        self.publication.update(visible=True,content_hash=body["content_hash"],
                                captured_utc=body["captured_utc"],synced_utc=SAVED)
        self.snapshots[UID] = {"revision":self.publication["revision"],"content":content,
                              "captured_utc":body["captured_utc"],"synced_utc":SAVED}
        return deepcopy(self.publication)

    def published(self, uid):
        if uid not in self.snapshots or uid==RIVAL and self.private:
            raise CommunityError("progress_unavailable",status=404)
        value = deepcopy(self.snapshots[uid])
        if uid==RIVAL and (self.partial or self.unsupported):
            value["content"]["coverage"].update(
                total_rows=len(value["content"]["rows"])+int(self.partial)+int(self.unsupported),
                withheld_rows=int(self.partial),unsupported_rows=int(self.unsupported),complete=False)
        return {"v":2,"uid":uid,**value,"content_hash":hashlib.sha256(canonical(value["content"])).hexdigest()}

    def player(self, uid):
        result = {"v":2,"uid":uid,"claimed_account":True,"sheet_verified":False,"sheet_runner":None,
            "name":self.state["name"] if uid==UID else LONG_PLAYER,
            "bio":self.profile["bio"] if uid==UID else "Learning one star at a time.",
            "socials":self.profile["socials"] if uid==UID else [{"kind":"twitch","url":"https://www.twitch.tv/fixture_player"}]}
        try:
            value = self.published(uid)
            result["progress"] = {"available":True,**{key:value[key] for key in ("revision","content_hash","captured_utc","synced_utc")}}
        except CommunityError:
            result["progress"] = {"available":False,"revision":0,"content_hash":None,"captured_utc":None,"synced_utc":None}
        return result

    async def request_json(self, method, path, **kwargs):
        body,params = kwargs.get("json"),kwargs.get("params",{})
        self.calls.append((method,path,deepcopy(body),deepcopy(params)))
        if self.state["status"]!="active":
            allowed = method=="GET" and path in ("/v2/account/progress","/v1/account/export","/v2/account/player-export")
            allowed = allowed or method=="PUT" and path=="/v2/account/progress/control" and body.get("action") in ("disable","withdraw")
            if not allowed:
                raise CommunityError("suspended","Active community access is unavailable.",status=403)
        if self.offline and path.startswith("/v2/players"):
            raise CommunityError("offline","Shared progress could not be reached.",status=503)
        if method!="GET":
            key = kwargs["headers"]["Idempotency-Key"]
            assert str(UUID(key))==key
            if key in self.receipts:
                assert self.receipts[key]==body
                return deepcopy(self.publication)
            self.writes.append((path,deepcopy(body)))
        result = self._route(method,path,body,params)
        if method!="GET":
            self.receipts[key] = deepcopy(body)
        return deepcopy(result)

    def _route(self, method, path, body, params):
        if path=="/v1/account/export":
            return {"v":1,"account":{"uid":UID,"name":self.state["name"]},"recordings":[]}
        if path=="/v2/account/player-export":
            return {"v":2,"uid":UID,"profile":self.profile,"publication":self.publication,
                    "snapshot":self.snapshots.get(UID)}
        if path=="/v2/account/progress":
            return self.publication
        if path=="/v2/account/progress/control":
            return self.control(body)
        if path=="/v2/account/progress/snapshot":
            return self.save_snapshot(body)
        if path=="/v2/account/player-profile":
            if method=="PUT":
                self._check_revision(body,self.profile)
                self.profile.update(revision=self.profile["revision"]+1,bio=body["bio"],socials=body["socials"])
            return self.profile
        if path=="/v2/players":
            players = [self.player(uid) for uid in (UID,RIVAL)]
            return {"v":2,"players":[row for row in players if params.get("prefix","").casefold() in row["name"].casefold()],
                    "next_cursor":None,"complete":True}
        if path.startswith("/v2/players/"):
            uid = path.split("/")[3]
            if path.endswith("/progress"):
                return self.published(uid)
            if path.endswith("/recordings"):
                return {"v":2,"uid":uid,"generation":"g1","recordings":[row for row in self.metadata if row["player_id"]==uid],"next_cursor":None,"complete":True}
            return self.player(uid)
        raise AssertionError(f"Unmatched offline account route: {method} {path}")

    def external_publisher(self):
        return self.control({"expected_revision":self.publication["revision"],"action":"switch",
            "notice_version":"progress-1","publisher":{"installation_id":"50000000-0000-4000-8000-000000000001",
            "profile_id":"60000000-0000-4000-8000-000000000001","label":"Another Trainer profile"}})


@dataclass
class PlayerBrowser:
    page: object
    base: str
    service: object
    session: LibrarySession
    account: Account
    progress: object
    log_path: Path

    def practice(self):
        return (self.service.target,self.service.strat_tag,len(self.service.db.events()),
                deepcopy(self.service.db.pbs()),deepcopy(self.service.rank_excluded()),
                deepcopy(self.session._rivals.options),self.session._rivals.rows,
                self.session._rivals.generation,len(self.session.native.preparations))

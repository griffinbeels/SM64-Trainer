"""Desired rival rosters and saved preferences; native preparation owns adoption."""
import json
from time import monotonic

from sm64_events.ghost.adaptive import (
    PracticeIdentity, add_slot, checked_options, rank_frames, rank_recordings, select_rivals,
    MAX_STEP, MAX_AHEAD, MAX_RIVALS,
)
from sm64_events.ghost.pb import saved_pb_basis
from sm64_events.ghost.sharing_targets import describe
from sm64_events.ranks.standards import entity_key

STATE_KEY = "ghost_rivals_v1"
PB_SETTLE_S = 5.0
LIMITS = {"last_step":MAX_STEP, "max_ahead":MAX_AHEAD, "counts":list(range(1,MAX_RIVALS+1))}


def wire(row):
    """Playback references and local paths never belong in a browser answer."""
    return {key:value for key,value in row.items() if key not in ("ghost", "path", "manifest", "source")}


class ColoredGhost:
    def __init__(self, ghost, hue):
        self.ghost = ghost
        self.hue_degrees = hue

    def __getattr__(self, name):
        return getattr(self.ghost, name)


class RivalProvider:
    """Certified population and the existing private import library share playback."""
    def __init__(self, population, sharing):
        self.population, self.sharing = population, sharing

    def resolve(self, ghost, target):
        try:
            self.population.get(ghost.import_id)
        except LookupError:
            if self.sharing is None:
                raise
            return self.sharing.resolve(ghost, target)
        return self.population.resolve(ghost, target)


class Rivals:
    def __init__(self, tracker, population, native, version_of, *, now=monotonic, source_of=lambda:None):
        self.tracker, self.population, self.native = tracker, population, native
        self.version_of, self.now = version_of, now
        self.source_of = source_of
        self.options = checked_options({})
        self.pins = {}
        self.local_choices = {}
        self.loaded_db = None
        self.target = None
        self.identity = None
        self.rows = ()
        self.pb = None
        self.generation = 0
        self.fingerprint = None
        self.pb_deadline = None
        self.pb_deadlines = {}
        self.status = None
        self.manual = False
        self.preserve_slots = False
        self.prepared_key = None
        self.sharing = None

    def identity_for(self, target, *, version=None):
        ranks = self.tracker.ranks
        version = self.version_of() if version is None else version
        if target is None or ranks is None or version not in ("us", "jp"):
            return None
        if target["kind"] == "star":
            course, star = map(int, target["entity_key"].split("-"))
            ek = entity_key(course,star)
            signature = None
        else:
            ek = entity_key(None,None,int(target["entity_key"]))
            signature = describe(target["kind"],target["entity_key"],target.get("strat"),
                                 self.tracker.db.segment_defs()).get("signature")
        return PracticeIdentity(target["kind"],target["entity_key"],ranks.clock_for(ek),
                                version,target.get("strat"),signature)

    def _key(self, target):
        return json.dumps([target["kind"],target["entity_key"]],separators=(",",":"))

    def _load(self):
        database = self.tracker.db
        if database is None or database is self.loaded_db:
            return
        stored = database.get_state(STATE_KEY,{})
        self.pins, self.local_choices = {},{}
        try:
            self.options = checked_options(stored.get("options",{}))
            pins = stored.get("pins",{})
            self.local_choices = stored.get("local_choices",{})
            if not isinstance(self.local_choices,dict):
                self.local_choices = {}
            if isinstance(pins,dict):
                self.pins = {key:{slot:value for slot,value in choices.items()
                    if slot in {"0","1","2","3"} and isinstance(value,str) and len(value)==64}
                    for key,choices in pins.items() if isinstance(key,str) and isinstance(choices,dict)}
            used = {identity for choices in self.pins.values() for identity in choices.values()}
            self.local_choices = {key:value for key,value in self.local_choices.items() if key in used}
        except (ValueError,AttributeError):
            self.options = checked_options({})
            self.pins = {}
        self.loaded_db = database
        self.fingerprint = self.prepared_key = None
        self.rows, self.pb = (),None
        self.manual = False
        self.pb_deadlines = {}

    def pb_changed(self, target=None):
        target = target or self.target
        if target is None:
            return
        key = self._key(target)
        deadline = self.now()+PB_SETTLE_S
        self.pb_deadlines[key] = deadline
        if self.target is None or self._key(self.target)!=key:
            return
        self.pb_deadline = deadline
        self.fingerprint = None
        self.prepared_key = None
        self.native.cancel_preparation()

    def refresh(self, target, controls, *, enabled, sharing=None, force=False):
        self._load()
        changed_target = target != self.target
        changed_entity = target is None or self.target is None or self._key(target)!=self._key(self.target)
        if changed_target:
            self.target = target
            self.fingerprint = None
            self.rows, self.pb = (),None
            self.manual = False
        self.pb_deadlines = {key:value for key,value in self.pb_deadlines.items() if value>self.now()}
        self.pb_deadline = self.pb_deadlines.get(self._key(target)) if target else None
        identity = self.identity_for(target)
        self.identity = identity
        self.sharing = sharing
        if self.manual:
            return None
        if identity is None:
            self.rows, self.pb = (),None
            self.fingerprint = self.prepared_key = None
            self.native.cancel_preparation()
            self.status = "Load a compatible practice ROM to choose rivals." if target else None
            return {"available":False,"reason":"unknown_rom" if target else "no_target",
                    "reason_text":self.status,"ghosts":[]}
        settled = self.pb_deadline is None or self.now()>=self.pb_deadline
        if not settled:
            self.status = "Updating rivals after the PB undo window."
            return ({"available":False,"reason":"pb_settling","reason_text":self.status,"ghosts":[]}
                    if changed_entity else None)
        self.pb_deadline = None
        source = self.source_of()
        stamp = (id(self.loaded_db),identity,enabled,source,controls["hue_degrees"],getattr(self.population,"generation",None),
                 getattr(self.tracker.ranks,"calibration_revision",None),
                 getattr(sharing,"generation",None),
                 json.dumps(self.options,sort_keys=True),json.dumps(self.pins,sort_keys=True))
        if force or stamp != self.fingerprint:
            pb = saved_pb_basis(self.tracker.db,self.tracker.ranks,identity)
            records = self.population.metadata_snapshot(identity.kind,identity.entity_key,
                clock=identity.clock,game_version=identity.game_version)
            ranked = rank_recordings(records,self.tracker.ranks,identity)
            pins = self.pins.get(self._key(target),{})
            private = [row for value in pins.values() if (row:=self._private_record(value,identity)) is not None]
            self.rows = select_rivals((*ranked,*private),pb,self.options,pins,
                                      self.rows if self.preserve_slots else ())
            self.preserve_slots = False
            self.pb = pb
            ghosts = [ColoredGhost(row["ghost"],row["hue_degrees"]) for row in self.rows if row.get("ghost") is not None]
            if pb and self.options["include_pb"] and pb.get("ghost") is not None:
                ghosts.insert(0,ColoredGhost(pb["ghost"],controls["hue_degrees"]))
                self.pb = {**pb,"hue_degrees":controls["hue_degrees"]}
            prepared_key = (id(self.loaded_db),identity,enabled,source,
                            tuple((ghost.origin,ghost.hue_degrees) for ghost in ghosts))
            self.fingerprint = stamp
            if prepared_key == self.prepared_key:
                return None
            self.prepared_key = prepared_key
            self.generation += 1
            self.native.set_portable(RivalProvider(self.population,sharing))
            answer = self.native.prepare(target,ghosts,controls,self.generation) if enabled else None
            self.status = "Updates on your next attempt." if ghosts else None
            return answer or {"available":bool(ghosts),"reason":None if ghosts else "no_rivals",
                              "reason_text":None if ghosts else "No compatible ghosts in this range yet.","ghosts":[]}
        return None

    def payload(self):
        native = self.native.state()
        if native.get("active_generation")==self.generation and self.pb_deadline is None:
            self.status = None
        return {"options":dict(self.options),"limits":dict(LIMITS),
                "rows":[wire(row) for row in self.rows],
                "pb":wire(self.pb) if self.pb else None,
                "strategy_name":self.target.get("strat") or "Standard" if self.target else None,
                "status":self.status,"generation":self.generation,"manual":self.manual}

    def search(self, target, player, cursor=None):
        identity = self.identity_for(target)
        if target != self.target or identity is None:
            raise ValueError("The practice target changed. Search for the current star or segment.")
        result = self.population.search(identity,player,cursor=cursor,limit=20)
        records = rank_recordings(result["records"],self.tracker.ranks,identity)
        if cursor is None and player.strip() and self.sharing is not None and len(records)<20:
            from unicodedata import normalize
            prefix = normalize("NFKC",player).casefold().strip()
            private = (self._private_record(ghost.id,identity) for ghost in
                       self.sharing.imports_for(identity.kind,identity.entity_key)
                       if normalize("NFKC",ghost.author).casefold().startswith(prefix))
            records = (*records,*(row for row in private if row is not None))[:20]
        return {"records":[wire(row) for row in records],"next_cursor":result.get("next_cursor")}

    def _private_record(self, identity, target):
        """An explicit private import may be pinned, but never earns admission."""
        local = self.local_choices.get(identity)
        if isinstance(local,dict):
            from sm64_events.ghost.pb import AttemptGhost, local_attempt_source
            from sm64_events.inputs.service import entity_key_of
            number = local.get("attempt_id")
            attempt = self.tracker.db.attempt(number) if type(number) is int else None
            if (attempt is None or local_attempt_source(attempt)!=local
                    or entity_key_of(attempt)!=(target.kind,target.entity_key)
                    or attempt.game_version not in (None,target.game_version)):
                return None
            frames = getattr(attempt,f"{target.clock}_frames")
            progress = rank_frames(self.tracker.ranks,target,frames)
            if progress is None:
                return None
            return {"id":identity,"player_id":"private:you","player_name":"You",
                    "strategy":attempt.strat_tag or "Standard","frames":frames,
                    "ghost":AttemptGhost(number,target.kind,target.entity_key,attempt.strat_tag,frames),
                    "manual_only":True,"current_strategy":target.strategy,**progress}
        if self.sharing is None:
            return None
        try:
            ghost = self.sharing.get(identity)
            row = self.sharing.portable_entry(identity)
        except (LookupError,ValueError):
            return None
        if ((ghost.kind,ghost.entity_key)!=(target.kind,target.entity_key)
                or row["clock"]!=target.clock or row.get("game_version") not in (None,target.game_version)):
            return None
        progress = rank_frames(self.tracker.ranks,target,ghost.frames)
        if progress is None:
            return None
        return {"id":ghost.id,"player_id":f"private:{ghost.author}","player_name":ghost.author,
                "strategy":ghost.strat_tag or "Standard","frames":ghost.frames,"ghost":ghost,
                "manual_only":True,"current_strategy":target.strategy,**progress}

    def _remember_local(self, source):
        from hashlib import sha256
        identity = sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()
        self.local_choices[identity] = source
        return identity

    def remember_saved(self, target, attempt):
        """Saving one run preserves other chosen slots, including hidden ones."""
        from sm64_events.ghost.pb import local_attempt_source
        self._load()
        source = local_attempt_source(attempt)
        if source is None:
            return False
        choices = self.pins.setdefault(self._key(target),{})
        if any(self.local_choices.get(identity) == source for identity in choices.values()):
            return True
        rows = {row["slot"]:row for row in self.rows} if target == self.target else {}
        # A settling target can have no displayed rows. Persisted choices still
        # own their slots and must never become automatic placement vacancies.
        rows.update({int(slot):{"slot":int(slot),"pinned":True} for slot in choices})
        try:
            slot = add_slot(rows.values(),self.options["count"])
        except ValueError:
            return False
        identity = self._remember_local(source)
        if identity not in choices.values():
            choices[str(slot)] = identity
        self.preserve_slots = True
        self.fingerprint = None
        self._store()
        return True

    def forget_saved(self, attempt):
        from sm64_events.ghost.pb import local_attempt_source
        self._load()
        source = local_attempt_source(attempt)
        forgotten = {key for key,value in self.local_choices.items() if value == source}
        self.pins = {key:{slot:identity for slot,identity in choices.items() if identity not in forgotten}
                     for key,choices in self.pins.items()}
        self.preserve_slots = True
        self.fingerprint = None
        self._store()

    def choose_library(self, target, ghosts, include_pb):
        """Explicit library choices become protected slots in the same roster."""
        from sm64_events.ghost.pb import local_attempt_source
        self._load()
        identity = self.identity_for(target)
        basis = saved_pb_basis(self.tracker.db,self.tracker.ranks,identity) if identity else None
        if sum(not getattr(ghost,"pb",False) or basis is None for ghost in ghosts)>MAX_RIVALS:
            raise ValueError("Choose up to four rivals; this historical PB also needs a rival slot.")
        self.target, self.identity = target,identity
        choices = {}
        for ghost in ghosts:
            if getattr(ghost,"pb",False) and basis is not None:
                continue
            identity = getattr(ghost,"import_id",None)
            if identity is None:
                from sm64_events.ghost.pb import attempt_id_of
                source = local_attempt_source(self.tracker.db.attempt(attempt_id_of(ghost.origin)))
                if source is None:
                    continue
                identity = self._remember_local(source)
            choices[str(len(choices))] = identity
        self.pins[self._key(target)] = choices
        self.options = {**self.options,"include_pb":include_pb,"count":max(self.options["count"],len(choices))}
        self.apply({})

    def library_limit(self, target=None):
        identity = self.identity_for(target or self.target)
        basis = saved_pb_basis(self.tracker.db,self.tracker.ranks,identity) if identity else None
        return MAX_RIVALS + int(basis is not None)

    def checked(self, body, target):
        if not isinstance(body,dict) or set(body)-{"options","action","id","slot"}:
            raise ValueError("Choose rival settings or a saved ghost.")
        if target != self.target or self.identity is None:
            raise ValueError("The practice target changed. Choose rivals for the current target.")
        result = {}
        if "options" in body:
            result["options"] = checked_options(body["options"])
        action = body.get("action")
        if action is not None:
            if action not in ("add","reset"):
                raise ValueError("Choose or reset a rival.")
            slot = body.get("slot")
            if slot is not None and (type(slot) is not int or not 0<=slot<self.options["count"]):
                raise ValueError("Choose a visible rival slot.")
            if action == "reset":
                if slot is None:
                    raise ValueError("Choose the rival to reset.")
                result.update(action=action,slot=slot)
            else:
                identity = body.get("id")
                if not isinstance(identity,str) or len(identity)!=64 or any(char not in "0123456789abcdef" for char in identity):
                    raise ValueError("Choose a saved ghost identity.")
                try:
                    row = self.population.get(identity)
                    ranked = rank_recordings([row],self.tracker.ranks,self.identity)
                except LookupError:
                    row = self._private_record(identity,self.identity)
                    ranked = (row,) if row else ()
                if not ranked:
                    raise ValueError("That ghost is not compatible with this target.")
                if any(item.get("id")==row["id"] for item in self.rows):
                    raise ValueError("That ghost is already in your rivals.")
                if row["id"] in self.pins.get(self._key(target),{}).values():
                    raise ValueError("That ghost is already chosen in a hidden rival slot. Increase the rival count to replace it.")
                slot = add_slot(self.rows,self.options["count"],slot)
                result.update(action=action,slot=slot,id=row["id"])
        return result

    def apply(self, body):
        if "options" in body:
            self.options = body["options"]
        choices = self.pins.setdefault(self._key(self.target),{})
        if body.get("action")=="reset":
            choices.pop(str(body["slot"]),None)
        elif body.get("action")=="add":
            choices[str(body["slot"])]=body["id"]
        self.manual = False
        self.preserve_slots = "options" not in body
        self.fingerprint = None
        self._store()

    def _store(self):
        if self.tracker.db is not None:
            used = {identity for values in self.pins.values() for identity in values.values()}
            self.local_choices = {key:value for key,value in self.local_choices.items() if key in used}
            self.tracker.db.set_state(STATE_KEY,{"version":1,"options":self.options,"pins":self.pins,
                                               "local_choices":self.local_choices})

"""Desired rival rosters and saved preferences; native preparation owns adoption."""
import json
from itertools import islice
from uuid import UUID

from sm64_events.ghost.adaptive import (
    PracticeIdentity, add_slot, checked_options, rank_frames, rank_recordings, select_rivals,
    MAX_STEP, MAX_AHEAD, MAX_RIVALS,
)
from sm64_events.ghost.pb import saved_pb_basis
from sm64_events.ghost.sharing_targets import describe
from sm64_events.ranks.standards import entity_key

STATE_KEY = "ghost_rivals_v1"
MAX_REMOTE_CHOICES = 1024
LIMITS = {"last_step":MAX_STEP, "max_ahead":MAX_AHEAD, "counts":list(range(1,MAX_RIVALS+1))}


def wire(row):
    """Playback references and local paths never belong in a browser answer."""
    return {key:value for key,value in row.items() if key not in ("ghost", "path", "manifest", "source")}


class ColoredGhost:
    def __init__(self, ghost, hue, rival_slot=None):
        self.ghost = ghost
        self.hue_degrees = hue
        self.rival_slot = rival_slot

    def __getattr__(self, name):
        return getattr(self.ghost, name)


class PersonalBestGhost:
    """The isolated tester can race its unchanged portable baseline clip."""
    pb = True

    def __init__(self, ghost):
        self.ghost = ghost

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
    def __init__(self, tracker, population, native, version_of, *, source_of=lambda:None, community=None):
        self.tracker, self.population, self.native = tracker, population, native
        self.version_of = version_of
        self.source_of = source_of
        self.options = checked_options({})
        self.pins = {}
        self.scopes = {}
        self.local_choices = {}
        self.remote_choices = {}
        self.loaded_db = None
        self.target = None
        self.identity = None
        self.rows = ()
        self.pb = None
        self.generation = 0
        self.fingerprint = None
        self.status = None
        self.manual = False
        self.preserve_slots = False
        self.prepared_key = None
        self.prepared_answer = None
        self.sharing = None
        from sm64_events.ghost.community_rivals import CommunityRivals
        self.community = CommunityRivals(community, population)

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
                                version,target.get("strat"),signature,self.scope_for(target))

    def scope_for(self, target):
        return self.scopes.get(self._entity_key(target),"strategy") if target else "strategy"

    def native_target(self, target=None):
        target = target or self.target
        return {**target,"scope":self.scope_for(target)} if target else None

    def _update_identity(self, target, controls, changed_target):
        identity = self.identity_for(target)
        changed_identity = self.identity != identity
        if changed_identity:
            self.rows, self.pb = (),None
            self.fingerprint = self.prepared_key = None
            self.manual = False
            self.community.downloading = False
        self.identity = identity
        if changed_target or changed_identity:
            self.native.reconcile(enabled=False,target=self.native_target(target),controls=controls)
        return changed_identity

    def _key(self, target):
        if self.scope_for(target)=="overall":
            return json.dumps([target["kind"],target["entity_key"],"overall",None],separators=(",",":"))
        return self._strategy_key(target)

    def _strategy_key(self, target):
        return json.dumps([target["kind"],target["entity_key"],target.get("strat") or "Standard"],separators=(",",":"))

    def _entity_key(self, target):
        """The entity owns its scope independently of the current strategy."""
        return json.dumps([target["kind"],target["entity_key"]],separators=(",",":"))

    def _migrate_pins(self, target):
        """Place old entity-wide choices only into their own strategy's slots."""
        legacy = self.pins.get(self._entity_key(target))
        if not legacy:
            return
        changed = False
        for slot, identity in tuple(legacy.items()):
            local = self.local_choices.get(identity)
            number = local.get("attempt_id") if isinstance(local,dict) else None
            attempt = self.tracker.db.attempt(number) if type(number) is int and number>0 else None
            if attempt is not None:
                strategy = attempt.strat_tag or "Standard"
            else:
                try:
                    strategy = self.population.get(identity).get("strategy") or "Standard"
                except (LookupError,ValueError):
                    try:
                        strategy = self.sharing.get(identity).strat_tag or "Standard"
                    except (LookupError,AttributeError,ValueError):
                        continue
            destination = {**target,"strat":strategy}
            choices = self.pins.setdefault(self._strategy_key(destination),{})
            if identity not in choices.values():
                vacancy = slot if slot not in choices else next((str(n) for n in range(MAX_RIVALS) if str(n) not in choices),None)
                if vacancy is None:
                    continue  # Preserve the old explicit choice until a slot is free.
                choices[vacancy] = identity
            legacy.pop(slot)
            changed = True
        if changed:
            if not legacy:
                self.pins.pop(self._entity_key(target),None)
            self._store()

    def _simulation(self):
        """A fixture marker is valid only inside its separately prepared profile."""
        from pathlib import Path
        from sm64_events.core.paths import practice_root
        state = self.tracker.db.get_state("ghost_simulation_v1",{})
        if not isinstance(state,dict) or state.get("version") != 1:
            return None
        try:
            root = Path(state["scenario_root"]).resolve()
            if root != practice_root():
                return None
        except (KeyError,TypeError,AttributeError,OSError,ValueError):
            return None
        return state

    def _simulation_pb(self, pb, target):
        state = self._simulation()
        if not pb or not state or state.get("target") != target:
            return pb
        baseline = state.get("baseline",{})
        if not isinstance(baseline,dict):
            return pb
        if (pb.get("pb_id"),pb.get("attempt_id"),pb.get("frames")) != (
                baseline.get("pb_id"),baseline.get("attempt_id"),baseline.get("frames")):
            return pb
        try:
            row = self.population.get(baseline.get("recording_id"))
        except (LookupError,ValueError):
            return pb
        if (row.get("simulated") is not True or row.get("certificate_source") != "developer_simulation"
                or row["frames"] != pb["frames"] or row["strategy"] != target.get("strat")
                or row.get("sha256") != baseline.get("sha256") or row.get("source") != baseline.get("source")
                or (row["kind"],row["entity_key"]) != (target["kind"],target["entity_key"])
                or row.get("game_version") != self.identity.game_version):
            return pb
        return {**pb,"ghost":PersonalBestGhost(row["ghost"])}

    def _load(self):
        database = self.tracker.db
        if database is None or database is self.loaded_db:
            return
        stored = database.get_state(STATE_KEY,{})
        self.pins, self.local_choices, self.remote_choices, self.scopes = {},{},{},{}
        try:
            self.options = checked_options(stored.get("options",{}))
            pins = stored.get("pins",{})
            self.local_choices = stored.get("local_choices",{})
            scopes = stored.get("scopes",{})
            if isinstance(scopes,dict):
                self.scopes = {key:value for key,value in scopes.items()
                               if isinstance(key,str) and value in ("strategy","overall")}
            if not isinstance(self.local_choices,dict):
                self.local_choices = {}
            if isinstance(pins,dict):
                self.pins = {key:{slot:value for slot,value in choices.items()
                    if slot in {"0","1","2","3"} and isinstance(value,str) and len(value)==64
                    and all(char in "0123456789abcdef" for char in value)}
                    for key,choices in pins.items() if isinstance(key,str) and isinstance(choices,dict)}
            used = {identity for choices in self.pins.values() for identity in choices.values()}
            self.local_choices = {key:value for key,value in self.local_choices.items() if key in used}
            # Older opaque IDs are private until live metadata proves otherwise.
            remote = stored.get("remote_choices", {}) if stored.get("version") == 4 else {}
            if isinstance(remote,dict):
                for identity,owner in islice(remote.items(), MAX_REMOTE_CHOICES):
                    if identity not in used or identity in self.local_choices:
                        continue
                    try:
                        if str(UUID(owner)) == owner:
                            self.remote_choices[identity] = owner
                    except (ValueError,TypeError,AttributeError):
                        continue
        except (ValueError,AttributeError):
            self.options = checked_options({})
            self.pins = {}
        self.loaded_db = database
        self.fingerprint = self.prepared_key = None
        self.rows, self.pb = (),None
        self.manual = False

    def pb_changed(self, target=None):
        target = target or self.target
        if target is None:
            return
        if self.target is None or self._entity_key(self.target)!=self._entity_key(target):
            return
        # The saved PB is authoritative, including Undo. Recompute now, but
        # retain an unchanged active/pending roster rather than canceling it.
        self.fingerprint = None

    def pb_preserved(self, attempt_id):
        """Retry a refused current PB only after its committed poses validate."""
        if (self.loaded_db is not self.tracker.db or self.identity is None
                or not self.pb or self.pb.get("available") is not False):
            return False
        ghost = self.pb.get("ghost")
        if getattr(ghost, "attempt_id", None) != attempt_id:
            return False
        current = saved_pb_basis(self.tracker.db, self.tracker.ranks, self.identity)
        if (not current or current.get("attempt_id") != attempt_id
                or current.get("source") != self.pb.get("source")
                or getattr(current.get("ghost"), "origin", None) != ghost.origin):
            return False
        if not self.native.probe(self.native_target(), ghost).get("available"):
            return False
        self.fingerprint = self.prepared_key = None
        return True

    def refresh(self, target, controls, *, enabled, sharing=None, force=False):
        self._load()
        changed_target = target != self.target
        if changed_target:
            self.target = target
            self.fingerprint = None
            self.rows, self.pb = (),None
            self.manual = False
        self._update_identity(target,controls,changed_target)
        identity = self.identity
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
        self._migrate_pins(target)
        self._request_community(enabled, identity, target)
        source = self.source_of()
        stamp = (id(self.loaded_db),identity,enabled,source,controls["hue_degrees"],getattr(self.population,"generation",None),
                 getattr(self.tracker.ranks,"calibration_revision",None),
                 getattr(sharing,"generation",None),
                 json.dumps(self.options,sort_keys=True),json.dumps(self.pins,sort_keys=True))
        if force or stamp != self.fingerprint:
            pb = saved_pb_basis(self.tracker.db,self.tracker.ranks,identity)
            pb = self._simulation_pb(pb,target)
            records = self.population.metadata_snapshot(identity.kind,identity.entity_key,
                clock=identity.clock,game_version=identity.game_version,
                strategy=identity.strategy if identity.scope=="strategy" else None)
            ranked = rank_recordings(records,self.tracker.ranks,identity)
            pins = self.pins.get(self._key(target),{})
            owners = self._owned_players()
            simulation = self._simulation()
            fresh_climb = simulation and simulation.get("zero_practice") is True
            # Explicit chosen rivals remain choices. Automatic slots never
            # use our own runs. The full-range developer climb also retires
            # beaten timings, including ones within the same subdivision.
            ranked = tuple(row for row in ranked if row["id"] in pins.values() or (
                row["player_id"] not in owners and (
                    not fresh_climb or not pb or row["frames"] < pb["frames"])))
            private = [row for value in pins.values() if (row:=self._private_record(value,identity)) is not None]
            desired = select_rivals((*ranked,*private),pb,self.options,pins,
                                    self.rows if self.preserve_slots else (),scope=identity.scope,
                                    allow_same_rank=self.community.complete(identity))
            self.rows = self.community.stage(desired, self.rows)
            self.preserve_slots = False
            self.pb = pb
            ghosts = [ColoredGhost(row["ghost"],row["hue_degrees"],row["slot"]) for row in self.rows if row.get("ghost") is not None]
            if pb and self.options["include_pb"] and pb.get("ghost") is not None:
                ghosts.insert(0,ColoredGhost(pb["ghost"],controls["hue_degrees"]))
                self.pb = {**pb,"hue_degrees":controls["hue_degrees"]}
            prepared_key = (id(self.loaded_db),identity,enabled,source,
                            tuple((ghost.origin,ghost.hue_degrees,ghost.rival_slot) for ghost in ghosts))
            self.fingerprint = stamp
            if prepared_key == self.prepared_key:
                self._pb_playback(self.prepared_answer)
                return None
            self.prepared_key = prepared_key
            self.generation += 1
            self.native.set_portable(RivalProvider(self.population,sharing))
            answer = self.native.prepare(self.native_target(target),ghosts,controls,self.generation) if enabled else None
            self.prepared_answer = answer
            self._pb_playback(answer)
            self.status = ("Updates on your next attempt."
                           if enabled and ghosts and answer and answer.get("available") else None)
            return (answer if ghosts else None) or {"available":bool(ghosts),"reason":None if ghosts else "no_rivals",
                              "reason_text":None if ghosts else "No compatible ghosts in this range yet.","ghosts":[]}
        return None

    def _pb_playback(self, answer):
        """Retain the rank anchor when its separate pose resolution fails."""
        if self.pb and answer:
            playback = next((row for row in answer.get("ghosts",()) if row.get("pb")),None)
            if playback:
                self.pb = {**self.pb,"available":playback["available"],
                           "reason_text":playback.get("reason_text")}

    def _request_community(self, enabled, identity, target):
        if enabled and self.community.sync is not None:
            basis = saved_pb_basis(self.tracker.db, self.tracker.ranks, identity)
            pins = self.pins.get(self._key(target), {})
            owner = self.community.owner()
            known = tuple(value for value in pins.values() if owner and self.remote_choices.get(value) == owner
                          and self._private_record(value,identity) is None)
            remote = self.community.request(self.tracker.ranks, identity, basis, self.options, pins, known_remote=known)
            if owner and any(self.remote_choices.get(value) != owner for value in remote):
                self.remote_choices.update((value,owner) for value in remote if value not in self.local_choices)
                self._store()

    def _owned_players(self):
        own = self.tracker.db.get_state('ghost_player_id', None)
        return self.population.owned_player_ids(own) if hasattr(self.population, 'owned_player_ids') else {own}

    def payload(self):
        native = self.native.state()
        if native.get("active_generation")==self.generation:
            self.status = None
        simulation = self._simulation()
        return {"options":dict(self.options),"limits":dict(LIMITS),"scope":self.scope_for(self.target),
                "simulation":simulation.get("label") if simulation else None,
                "rows":[wire(row) for row in self.rows],
                "pb":wire(self.pb) if self.pb else None,
                "strategy_name":self.target.get("strat") or "Standard" if self.target else None,
                "coverage": self.community.coverage(self.identity) if self.identity is not None else None,
                "status": self.community.status(self.identity) or (
                    self.status if native.get("prepared_generation")==self.generation else None),
                "generation":self.generation,"manual":self.manual}

    def search(self, target, player, cursor=None, *, scope=None):
        identity = self.identity_for(target)
        if target != self.target or identity is None:
            raise ValueError("The practice target changed. Search for the current star or segment.")
        if scope is not None and scope!=identity.scope:
            raise ValueError("The ghost view changed. Search in the current Strategy or Overall view.")
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
                    or target.scope=="strategy" and (attempt.strat_tag or "Standard") != (target.strategy or "Standard")
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
                or target.scope=="strategy" and (ghost.strat_tag or "Standard") != (target.strategy or "Standard")
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

    def check_library(self, target, ghosts):
        """Validate the roster before a session persists any accompanying values."""
        identity = self.identity_for(target)
        if self.scope_for(target)=="strategy" and any((getattr(ghost,"strat_tag",None) or "Standard") != (target.get("strat") or "Standard") for ghost in ghosts):
            raise ValueError("Choose ghosts for the strategy you are practicing.")
        basis = saved_pb_basis(self.tracker.db,self.tracker.ranks,identity) if identity else None
        dedicated = getattr(basis.get("ghost"),"origin",None) if basis else None
        def own_pb(ghost):
            return getattr(ghost,"pb",False) and dedicated is not None and ghost.origin==dedicated
        if sum(not own_pb(ghost) for ghost in ghosts)>MAX_RIVALS:
            raise ValueError("Choose up to four rivals; this historical PB also needs a rival slot.")
        return identity,dedicated

    def choose_library(self, target, ghosts, include_pb):
        """Explicit library choices become protected slots in the same roster."""
        from sm64_events.ghost.pb import local_attempt_source
        self._load()
        identity,dedicated = self.check_library(target,ghosts)
        self.target, self.identity = target,identity
        choices = {}
        for ghost in ghosts:
            if getattr(ghost,"pb",False) and dedicated is not None and ghost.origin==dedicated:
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

    def _checked_scope(self, body, target):
        if "scope" not in body:
            return {}
        if body["scope"] not in ("strategy","overall"):
            raise ValueError("Choose Strategy or Overall ghosts.")
        if body["scope"]!=self.scope_for(target) and set(body)!={"scope"}:
            raise ValueError("The ghost view changed. Reopen this choice in the current view.")
        return {"scope":body["scope"]}

    def checked(self, body, target):
        if not isinstance(body,dict) or set(body)-{"options","action","id","slot","scope"}:
            raise ValueError("Choose rival settings or a saved ghost.")
        if target != self.target or self.identity is None:
            raise ValueError("The practice target changed. Choose rivals for the current target.")
        result = self._checked_scope(body,target)
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
        if "scope" in body:
            self.scopes[self._entity_key(self.target)] = body["scope"]
        if "options" in body:
            self.options = body["options"]
        choices = self.pins.setdefault(self._key(self.target),{})
        if body.get("action")=="reset":
            choices.pop(str(body["slot"]),None)
        elif body.get("action")=="add":
            choices[str(body["slot"])]=body["id"]
            owner = self.community.owner()
            if owner:
                try:
                    row = self.population.get(body["id"])
                except (LookupError,ValueError):
                    row = {}
                if row.get("provenance") == "community-unverified":
                    self.remote_choices[body["id"]] = owner
        self.manual = False
        self.preserve_slots = "options" not in body
        self.fingerprint = None
        self._store()

    def _store(self):
        if self.tracker.db is not None:
            used = {identity for values in self.pins.values() for identity in values.values()}
            self.local_choices = {key:value for key,value in self.local_choices.items() if key in used}
            self.remote_choices = {key:value for key,value in self.remote_choices.items()
                if key in used and key not in self.local_choices}
            while len(self.remote_choices) > MAX_REMOTE_CHOICES:
                self.remote_choices.pop(next(iter(self.remote_choices)))
            self.tracker.db.set_state(STATE_KEY,{"version":4,"options":self.options,"pins":self.pins,"scopes":self.scopes,
                                               "local_choices":self.local_choices,"remote_choices":self.remote_choices})

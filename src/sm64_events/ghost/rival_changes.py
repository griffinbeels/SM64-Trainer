"""Saved rival commands share the roster owner's state without adopting a target.

Only the existing owner writes the complete preferences map. Metadata may be
unavailable: its chosen seat stays protected and is explicitly shown as such.
"""
from sm64_events.ghost.adaptive import MAX_RIVALS, add_slot, player_key, rank_frames
from sm64_events.ghost.portable import checked_hash
from sm64_events.ghost.sharing_targets import checked_target, describe


def _target_context(rivals, target, version, scope=None):
    if (not isinstance(target,dict) or set(target)!={"kind","entity_key","strat"}
            or not isinstance(target.get("strat"),str) or not target["strat"].strip()):
        raise ValueError("Choose a star or segment and its strategy.")
    # Star validation is global. A segment's signature is resolved from local
    # definitions, never supplied by a foreign collection or a browser.
    if target["kind"] == "segment":
        describe(target["kind"],target["entity_key"],target["strat"],rivals.tracker.db.segment_defs())
    else:
        checked_target(target)
    scope = rivals.scope_for(target) if scope is None else scope
    if scope not in ("strategy","overall"):
        raise ValueError("Choose Strategy or Overall ghosts.")
    identity = rivals.identity_for(target,version=version,scope=scope)
    if identity is None:
        raise ValueError("Choose a compatible practice ROM for this ghost.")
    return identity


def _grading_reason(rivals, context, expected_clock=None):
    if expected_clock is not None and expected_clock not in ("igt","rta"):
        return "This ghost does not identify a supported timing clock."
    if expected_clock is not None and expected_clock!=context.clock:
        return f"This ghost uses {expected_clock.upper()}; this target uses {context.clock.upper()}."
    # Exercise the actual grading resolver: an identity alone can exist when
    # this installation has no ladder for a newly discovered ghost method.
    if rank_frames(rivals.tracker.ranks,context,1) is None:
        return "This target has no local rank standards for that strategy yet."
    return None


def saved_availability(rivals, target, *, version=None, scope=None, expected_clock=None):
    rivals._load()
    try:
        context = _target_context(rivals,target,version,scope)
        reason = _grading_reason(rivals,context,expected_clock)
    except (LookupError,ValueError):
        return {"available":False,"reason":"This ghost needs a compatible local star or segment definition and practice ROM.",
                "pins":{}}
    return {"available":reason is None,"reason":reason,
            "pins":dict(rivals.pins.get(rivals._scope_key(target,context.scope),{}))}


def _context(rivals, target, body, version):
    if not isinstance(body,dict) or set(body)-{"action","id","slot","scope"}:
        raise ValueError("Choose a saved ghost to add or remove.")
    if body.get("action") not in ("add","remove"):
        raise ValueError("Choose a saved ghost to add or remove.")
    checked_hash(body.get("id"))
    if "scope" in body and body["scope"] not in ("strategy","overall"):
        raise ValueError("Choose Strategy or Overall ghosts.")
    identity = _target_context(rivals,target,version,body.get("scope"))
    if body["action"] == "add" and (reason:=_grading_reason(rivals,identity)) is not None:
        raise ValueError(reason)
    slot = body.get("slot")
    if slot is not None and (type(slot) is not int or not 0<=slot<MAX_RIVALS):
        raise ValueError("Choose an available ghost slot.")
    return identity,rivals._scope_key(target,identity.scope)


def _records(rivals, context, choices):
    rows = {}
    # Only the active context has automatic seats. An inactive target never
    # needs a provisional deal, a saved-PB read or native preparation.
    if context == rivals.identity:
        rows.update((row["slot"],rivals._seat_row(row)) for row in rivals.rows)
    for slot,identifier in choices.items():
        try:
            row = rivals._compatible_record(identifier,context)
        except (LookupError,ValueError):
            row = None
        rows[int(slot)] = ({**rivals._seat_row(row),"slot":int(slot),"pinned":True} if row else
                           {"id":identifier,"slot":int(slot),"pinned":True,"available":False,
                            "reason_text":"This saved ghost is unavailable for this target."})
    return rows


def _answer(rivals, target, context, choices, rows, state, identifier, slot):
    seats = []
    for number in range(MAX_RIVALS):
        row = rows.get(number)
        seat = {"slot":number,"visible":number<rivals.options["count"],
                "pinned":str(number) in choices,"id":row.get("id") if row else None,
                "available":bool(row and row.get("ghost") is not None)}
        if row:
            seat.update((key,row[key]) for key in ("player_id","player_name","frames","strategy",
                        "tier","division","reason_text") if key in row)
            seat["own"] = row.get("_owned") is True
        seats.append(seat)
    return {"state":state,"target":dict(target),"scope":context.scope,
            "pins":dict(choices),"seats":seats,"slot":slot,"id":identifier}


def _add(rivals, context, choices, rows, identifier, slot):
    row = rivals._compatible_record(identifier,context)
    if row is None or identifier in rivals.community.refused():
        raise ValueError("That ghost is not compatible with this target.")
    rivals.check_rival_owner(row)
    if identifier in choices.values():
        raise ValueError("That ghost is already chosen in a ghost slot, including hidden slots.")
    if any(item.get("id")==identifier for item in rows.values()):
        raise ValueError("That ghost is already selected.")
    candidate = rivals._seat_row(row)
    # A replacement can release this player's current seat, but neither a
    # hidden pin nor a different automatic seat may become a second player.
    duplicate = [number for number,item in rows.items() if item.get("ghost") is not None
                 and player_key(rivals._seat_row(item))==player_key(candidate)]
    if any(number!=slot for number in duplicate):
        raise ValueError("A ghost from that player is already selected. Replace their current slot.")
    if slot is not None:
        selected = add_slot(rows.values(),rivals.options["count"],slot)
    elif all(str(number) in choices for number in range(rivals.options["count"])):
        return row,None
    else:
        selected = add_slot(rows.values(),rivals.options["count"])
    return row,selected


def change_saved(rivals, target, body, *, version=None):
    """Validate and atomically persist one pin namespace in the owner's map."""
    rivals._load()
    context,key = _context(rivals,target,body,version)
    choices = dict(rivals.pins.get(key,{}))
    rows = _records(rivals,context,choices)
    identifier,slot = body["id"],body.get("slot")
    if body["action"] == "add":
        row,slot = _add(rivals,context,choices,rows,identifier,slot)
        if slot is None:
            return _answer(rivals,target,context,choices,rows,"needs_slot",identifier,None)
        choices[str(slot)] = identifier
        owner = rivals.community.owner()
        if owner and row.get("provenance") == "community-unverified":
            rivals.remote_choices[identifier] = owner
        state = "added"
    else:
        found = next((int(number) for number,value in choices.items() if value==identifier),None)
        if found is None or slot is not None and slot!=found:
            raise ValueError("That saved ghost changed. Reopen its choices.")
        slot = found
        choices.pop(str(slot))
        state = "removed"
    rivals.pins[key] = choices
    rivals._store()
    # Inactive changes do not invalidate Practice's cached rank basis or deal.
    if rivals.target is not None and key==rivals._key(rivals.target):
        rivals.fingerprint = None
    rows = _records(rivals,context,choices)
    return _answer(rivals,target,context,choices,rows,state,identifier,slot)

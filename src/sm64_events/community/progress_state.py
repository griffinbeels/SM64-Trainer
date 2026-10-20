"""Local publisher identity and strict control state; no saved snapshot queue."""
from pathlib import Path
from uuid import UUID, uuid4

from .config import CommunityError

KEY = "community_progress_v1"
PROFILE_KEY = "community_progress_profile_v1"
DEFAULT_CHOICES_KEY = "community_progress_default_choices_v1"
NOTICE = "progress-1"
NOTICE_TEXT = ("Your current saved personal bests are shared by default, with their strategies, game regions and available source/dates "
               "on your claimed player page. Other players can compare them with their own times. "
               "This does not share your attempts, practice history, private segment definitions or recordings. "
               "Pausing stops future updates; removing published PBs hides the saved snapshot.")


def virgin_publication(value):
    """Only an untouched server control can acquire the new default policy."""
    return (value.get("revision") == 0 and value.get("consent_revision") == 0 and value.get("publisher") is None
            and not value.get("enabled") and not value.get("visible") and value.get("content_hash") is None)


def default_choice(database, owner):
    values = database.get_state(DEFAULT_CHOICES_KEY, {})
    value = values.get(owner) if isinstance(values, dict) else None
    return value if type(value) is bool else None


def remember_choice(database, owner, choice):
    checked_uuid(owner)
    if type(choice) is not bool:
        raise CommunityError("invalid_progress", "Choose whether to share your saved PBs.")
    values = database.get_state(DEFAULT_CHOICES_KEY, {})
    values = dict(values) if isinstance(values, dict) else {}
    if values.get(owner) is not choice:
        values[owner] = choice
        database.set_state(DEFAULT_CHOICES_KEY, values)
        return True
    return False


def display_error(code):
    if code is None:
        return None
    return {"publisher_conflict": "Another Trainer profile publishes this account. Review it before switching.",
            "revision_conflict": "Publication changed. Refresh these settings before trying again.",
            "snapshot_too_large": "This profile exceeds the publication limit. Your local PBs remain saved.",
            "storage_limit": "Community publication storage is full. Your local PBs remain saved.",
            "progress_disabled": "PB updates are paused. Review the sharing preview to enable them."}.get(
                code, "Your latest saved PBs have not synced yet. Try again when the community service is reachable.")


def checked_uuid(value):
    try:
        if str(UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise CommunityError("invalid_response", "This player identity could not be read.")


def installation_id(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as stream:
            value = str(uuid4())
            stream.write(value)
            return value
    except FileExistsError:
        return checked_uuid(path.read_text(encoding="ascii").strip())


def publisher(database, installation):
    identifier = database.get_state(PROFILE_KEY, None)
    if identifier is None:
        identifier = str(uuid4())
        database.set_state(PROFILE_KEY, identifier)
    return {"installation_id": checked_uuid(installation), "profile_id": checked_uuid(identifier),
            "label": "This Trainer profile"}


def same_publisher(left, right):
    return bool(left and right and all(left.get(key) == right.get(key)
                                      for key in ("installation_id", "profile_id")))


def checked_control(value, uid):
    fields = {"v", "uid", "revision", "consent_revision", "enabled", "visible", "publisher",
              "notice_version", "content_hash", "captured_utc", "synced_utc", "limits"}
    if (not isinstance(value, dict) or set(value) != fields or type(value["v"]) is not int or value["v"] != 2 or value["uid"] != uid
            or any(type(value[key]) is not int or value[key] < 0 for key in ("revision", "consent_revision"))
            or any(type(value[key]) is not bool for key in ("enabled", "visible"))
            or value["notice_version"] != NOTICE or value["limits"] != {"rows": 4096, "bytes": 1048576}
            or any(type(value["limits"][key]) is not int for key in ("rows", "bytes"))):
        raise CommunityError("invalid_response")
    identity = value["publisher"]
    if identity is not None:
        if not isinstance(identity, dict) or set(identity) != {"installation_id", "profile_id", "label"}:
            raise CommunityError("invalid_response")
        checked_uuid(identity["installation_id"])
        checked_uuid(identity["profile_id"])
        if not isinstance(identity["label"], str) or len(identity["label"].encode()) > 128:
            raise CommunityError("invalid_response")
    from sm64_events.ghost.portable import checked_hash
    if value["content_hash"] is not None:
        try:
            checked_hash(value["content_hash"])
        except ValueError:
            raise CommunityError("invalid_response") from None
    if any(value[key] is not None and (not isinstance(value[key], str) or len(value[key]) > 40)
           for key in ("captured_utc", "synced_utc")):
        raise CommunityError("invalid_response")
    return dict(value)

"""Account names fit the native wire without changing a player's identity.

`ui/communityname.js` is the JavaScript twin; `tests/test_community_names.py`
pins both. The minimum comes from Speedrun.com's own sign-up form, which
refuses a name shorter than 4 characters (its signup page code, read
2026-10-12); his rule: "we should use the same username rules as Speedrun.com".
"""
import unicodedata

MIN_NAME_CHARACTERS = 4
MAX_NAME_CHARACTERS = 20
MAX_NAME_BYTES = 63
_UNSAFE_CATEGORIES = {"Cc", "Cf", "Cs", "Zl", "Zp"}
RESERVED_NAMES = frozenset({"admin", "administrator", "moderator", "sm64 trainer", "deleted"})
"""The cloud refuses these names (`cloud/community/src/names.js` checkedName)."""


def checked_name(value: str) -> str:
    """Apply limits after compatibility normalization, including expansions."""
    if not isinstance(value, str) or any(unicodedata.category(char) in _UNSAFE_CATEGORIES for char in value):
        raise ValueError("Use a player name without control characters.")
    if len(value.encode("utf-8")) > 128:
        raise ValueError("Player name input is too long.")
    name = unicodedata.normalize("NFKC", value).strip()
    if not name:
        raise ValueError("Enter your player name.")
    if len(name) < MIN_NAME_CHARACTERS:
        raise ValueError(f"Use at least {MIN_NAME_CHARACTERS} characters for your player name.")
    if len(name) > MAX_NAME_CHARACTERS or len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise ValueError("Use at most 20 characters and 63 UTF-8 bytes for your player name.")
    return name


def incoming_name(value) -> str:
    """A name from the cloud, held to the rule the cloud itself enforces.

    The cloud's checkedName refuses hidden controls and format characters
    (bidi overrides among them), more than 128 input bytes, more than 20
    normalized characters or 63 bytes, and its reserved names. It has no
    minimum length, so older short names still pass. Raises ValueError.
    """
    if (not isinstance(value, str) or any(unicodedata.category(char) in _UNSAFE_CATEGORIES for char in value)
            or len(value.encode("utf-8")) > 128):
        raise ValueError("This player name is not one the community service accepts.")
    name = unicodedata.normalize("NFKC", value).strip()
    if (not name or len(name) > MAX_NAME_CHARACTERS or len(name.encode("utf-8")) > MAX_NAME_BYTES
            or name.casefold() in RESERVED_NAMES):
        raise ValueError("This player name is not one the community service accepts.")
    return name

"""Account names fit the native wire without changing a player's identity."""
import unicodedata

MAX_NAME_CHARACTERS = 20
MAX_NAME_BYTES = 63
_UNSAFE_CATEGORIES = {"Cc", "Cf", "Cs", "Zl", "Zp"}


def checked_name(value: str) -> str:
    """Apply limits after compatibility normalization, including expansions."""
    if not isinstance(value, str) or any(unicodedata.category(char) in _UNSAFE_CATEGORIES for char in value):
        raise ValueError("Use a player name without control characters.")
    if len(value.encode("utf-8")) > 128:
        raise ValueError("Player name input is too long.")
    name = unicodedata.normalize("NFKC", value).strip()
    if not name:
        raise ValueError("Enter your player name.")
    if len(name) > MAX_NAME_CHARACTERS or len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise ValueError("Use at most 20 characters and 63 UTF-8 bytes for your player name.")
    return name

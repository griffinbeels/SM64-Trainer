"""Native ghost readability preferences, independent of the footage overlay."""
import math

DEFAULT_PROXIMITY = {"enabled": True, "extra_distance": 0.0}
PROXIMITY_KEY = "ghost_proximity"
MAX_EXTRA_DISTANCE = 400.0


def checked_proximity(value):
    """Validate a partial update without resetting the untouched preference."""
    if not isinstance(value, dict) or set(value) - set(DEFAULT_PROXIMITY):
        raise ValueError("Ghost overlap fading has an on/off switch and an extra distance.")
    checked = {}
    if "enabled" in value:
        if not isinstance(value["enabled"], bool):
            raise ValueError("Ghost overlap fading is either on or off.")
        checked["enabled"] = value["enabled"]
    if "extra_distance" in value:
        distance = value["extra_distance"]
        if (isinstance(distance, bool) or not isinstance(distance, (int, float))
                or not math.isfinite(distance) or not 0 <= distance <= MAX_EXTRA_DISTANCE):
            raise ValueError(f"Extra fade distance must be between 0 and {MAX_EXTRA_DISTANCE:g} game units.")
        checked["extra_distance"] = float(distance)
    return checked


def saved_proximity(value):
    """A malformed stored preference must not stop practice from opening."""
    try:
        return {**DEFAULT_PROXIMITY, **checked_proximity(value)}
    except ValueError:
        return dict(DEFAULT_PROXIMITY)

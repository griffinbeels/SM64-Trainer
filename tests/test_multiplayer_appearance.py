"""Friends display follows the existing appearance registry without resolving IO."""
import pytest

from sm64_events.ghost import settings
from sm64_events.ghost.session import GhostSession
from sm64_events.multiplayer.native_protocol import Appearance


def test_live_preferences_use_the_same_registry_without_payload_or_library():
    session = object.__new__(GhostSession)
    session._values = settings.defaults()
    session._values.update({"enabled": False, "opacity": .42, "names_enabled": False,
                           "name_background": "white", "record_ghosts": True})
    result, hidden = session.live_appearance()
    assert hidden and result.opacity == .42 and not result.names_enabled
    assert result.name_background == 1 and result.record_ghosts
    result.native()
    session._values["enabled"] = True
    assert session.live_appearance()[1] is False


def test_background_values_match_the_actual_native_enum():
    assert Appearance().native().name_background == 0
    with pytest.raises(ValueError):
        Appearance(name_background=2).native()

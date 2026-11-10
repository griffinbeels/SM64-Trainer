"""Friends observations cannot collect private room or account text."""
from sm64_events.core.uilog import sanitize


def test_friends_log_keeps_only_bounded_counts_and_fixed_phase():
    assert sanitize({"surface": "friends", "phase": "connected", "members": 4, "invitations": 1,
        "title": "private player", "code": "private address", "note": "private room"}) == {
            "surface": "friends", "phase": "connected", "members": 4, "invitations": 1}
    for key, value in (("phase", "private room"), ("members", 5), ("members", True), ("invitations", 17)):
        assert sanitize({"surface": "friends", "phase": "connected", "members": 4,
                         "invitations": 1, key: value}) is None

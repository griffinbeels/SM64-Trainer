"""Overlap tuning survives partial updates and reaches only native playback."""
import asyncio

import pytest

from sm64_events.ghost.proximity import DEFAULT_PROXIMITY, saved_proximity
from sm64_events.ghost.session import validate_changes
from tests.test_ghost_session import a_session


@pytest.mark.parametrize("value", [None, [], {"unknown": 1}, {"enabled": 1},
    {"extra_distance": True}, {"extra_distance": -1}, {"extra_distance": 401},
    {"extra_distance": float("nan")}, {"extra_distance": float("inf")}])
def test_invalid_distance_never_reaches_renderer(value):
    with pytest.raises(ValueError):
        validate_changes({"proximity": value})
    assert saved_proximity(value) == DEFAULT_PROXIMITY


def test_partial_tuning_survives_reload_and_view_changes(tmp_path):
    session, _, tracker, _ = a_session(tmp_path)

    async def check():
        await session.update({"proximity": {"extra_distance": 120}})
        await session.update({"proximity": {"enabled": False}})
        assert session.payload()["proximity"] == {"enabled": False, "extra_distance": 120}
        session._loaded = False
        await session.update({"view": "mario"})
        assert session.payload()["proximity"] == {"enabled": False, "extra_distance": 120}
        await session.update({"proximity": {"enabled": True}, "view": "whole"})
        assert session.payload()["proximity"] == {"enabled": True, "extra_distance": 120}
        await session.stop()

    asyncio.run(check())

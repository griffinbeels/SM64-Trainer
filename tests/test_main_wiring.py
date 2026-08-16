"""main.py wires ONE layout for the effective version; US until the setting
lands (feature/game-version's core/modes.py)."""
import sys

from sm64_events.main import _game_version, build_detectors


def test_build_detectors_takes_a_version_and_the_chain_is_unchanged_by_it():
    us = [type(d).__name__ for d in build_detectors()]
    assert us == [type(d).__name__ for d in build_detectors(version="us")]
    assert us == [type(d).__name__ for d in build_detectors(version="jp")]
    assert us[0] == "StarGrabDetector"


def test_effective_version_is_us_until_the_setting_module_exists(monkeypatch):
    if "sm64_events.core.modes" in sys.modules or _has_modes():
        # feature/game-version has landed: its effective_version decides.
        assert _game_version() in ("us", "jp")
    else:
        assert _game_version() == "us"


def _has_modes() -> bool:
    try:
        import sm64_events.core.modes  # noqa: F401
        return True
    except ImportError:
        return False

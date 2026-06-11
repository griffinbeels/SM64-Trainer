"""Tests for the Usamune timer display formatter."""
from sm64_events.core.timefmt import format_igt


def test_format_igt():
    assert format_igt(0) == "0'00\"00"
    assert format_igt(231) == "0'07\"70"
    assert format_igt(1800) == "1'00\"00"
    assert format_igt(1800 + 65) == "1'02\"16"

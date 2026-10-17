"""The JP layout for tests of the native ghost path.

Before its sync run this was a hand-built candidate layout; since 2026-10-17
memory/layout.py's JP row is verified, so the tests use the shipped one.
"""
from sm64_events.memory.layout import layout_for

JP_CANDIDATE = layout_for("jp")

"""The collapsed sweep must actually collapse something.

A story whose setup silently does nothing passes every assertion the sweep can
make, and looks exactly like a story that found no defects. That is the same
green-forever shape as a skipped gate, so the collapsed layout gets a test that
its setup REACHES a different page — measured, not assumed.

Cheap on purpose: one viewport, one toggle round trip. The sweep covers the
matrix; this covers the premise the sweep rests on.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from test_responsive import _MISSING  # noqa: E402  (shared uilab resolution)

if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from uilab_project import PROJECT, STORIES  # noqa: E402

_BY_NAME = {story.name: story for story in STORIES}


def _heights(page) -> list[int]:
    return page.evaluate(
        "([...document.querySelectorAll('.practice-card')]"
        ".map((c) => Math.round(c.getBoundingClientRect().height)))")


@pytest.fixture(scope="module")
def page():
    with PROJECT.open() as url, get_driver().launch() as opened:
        opened.goto(url)
        opened.wait_for(PROJECT.ready_selector)
        opened.set_viewport(900, 1180)          # the size the user reported
        opened.wait_ms(400)
        yield opened


def test_the_page_has_collapse_toggles_at_all(page):
    """Control. Everything below is vacuous if the app renders none."""
    assert page.count(".card-collapse") > 0, (
        "no collapse controls rendered — the collapsed story would be a no-op "
        "that passes")


def test_the_collapsed_story_reaches_a_shorter_page(page):
    open_heights = _heights(page)
    page.evaluate(_BY_NAME["page-collapsed"].setup)
    page.wait_ms(500)
    collapsed_heights = _heights(page)

    assert sum(collapsed_heights) < sum(open_heights), (
        f"collapsing changed nothing: {sum(open_heights)}px of cards before, "
        f"{sum(collapsed_heights)}px after. The collapsed sweep would be "
        f"measuring the open page and reporting it clean.")
    assert page.evaluate(
        "(document.querySelectorAll('.practice-card.is-collapsed').length)") > 0


def test_the_open_story_puts_it_back(page):
    """Order-independence: the sweep runs stories in a loop without reloading,
    so a setup that only works from one starting state corrupts its neighbours."""
    page.evaluate(_BY_NAME["page-collapsed"].setup)
    page.wait_ms(400)
    page.evaluate(_BY_NAME["page"].setup)
    page.wait_ms(500)
    assert page.evaluate(
        "(document.querySelectorAll('.practice-card.is-collapsed').length)") == 0


def test_both_setups_are_idempotent(page):
    """Running one twice must not invert the page — the reason each clicks only
    the toggles whose `aria-expanded` is wrong, rather than all of them."""
    page.evaluate(_BY_NAME["page-collapsed"].setup)
    page.wait_ms(300)
    once = page.evaluate(
        "(document.querySelectorAll('.practice-card.is-collapsed').length)")
    page.evaluate(_BY_NAME["page-collapsed"].setup)
    page.wait_ms(300)
    twice = page.evaluate(
        "(document.querySelectorAll('.practice-card.is-collapsed').length)")
    assert once == twice > 0, f"collapse setup is not idempotent: {once} -> {twice}"
    page.evaluate(_BY_NAME["page"].setup)

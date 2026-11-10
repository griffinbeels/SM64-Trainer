"""The setup check's Ghost Lab ROM row on screen, found and not found, through the real app fixture.

The row's words come from the real `SetupProfile` and `RomStore.held` (test_setup_lab_rom.py builds them); the
app serves them through `/api/setup/check` as test_ui_setup_check.py does. Screenshots land in pytest's tmp folder.
"""
import os
from pathlib import Path

import pytest

from test_ui_setup_check import CHECK, ITEMS, NOTICE, SETTINGS_CONTROL, opened, served  # noqa: F401 - fixtures
from test_setup_lab_rom import check_with, held
from tests.browser_faults import no_faults

EVIDENCE = os.environ.get("P5_EVIDENCE")   # a folder to copy screenshots into for a person to read


def shoot(page, tmp_path, name):
    image = page.locator(".modal").screenshot()
    (tmp_path / name).write_bytes(image)
    if EVIDENCE:
        (Path(EVIDENCE) / name).write_bytes(image)


def open_check(page):
    page.locator(SETTINGS_CONTROL).first.click()
    page.get_by_role("button", name="Check my setup").click()
    page.locator(f'{CHECK} [data-item="lab_rom"]').wait_for()


@pytest.mark.parametrize("width", (850, 1500))
def test_a_missing_us_rom_is_one_row_with_the_pick_and_plain_words(served, width, tmp_path):  # noqa: F811
    check = check_with(tmp_path, held(tmp_path))
    with opened(served, width, stalled=False, check=check) as (page, seen):
        open_check(page)
        row = page.locator(f'{CHECK} [data-item="lab_rom"]')
        text = row.inner_text()
        assert "Usamune ROM for the Ghost Lab" in text and "Not found" in text
        assert "The Ghost Lab needs Super Mario 64 Usamune v1.93u" in text
        assert "never uploads it" in text
        assert row.get_by_role("button", name="Choose ROM file").is_visible()
        items = page.evaluate(ITEMS, CHECK)
        assert items[0]["id"] == "lab_rom", "the one difference comes first"
        assert all(item["inside"] and not item["overflow"] for item in items), items
        shoot(page, tmp_path, f"setup-check-lab-rom-missing-{width}.png")
        no_faults(seen)


@pytest.mark.parametrize("width", (850, 1500))
def test_a_found_rom_reads_same_and_offers_nothing(served, width, tmp_path):  # noqa: F811
    check = check_with(tmp_path, held(tmp_path, "us", "jp"))
    with opened(served, width, stalled=False, check=check) as (page, seen):
        open_check(page)
        row = page.locator(f'{CHECK} [data-item="lab_rom"]')
        assert row.get_attribute("data-status") == "match"
        assert "Found: v1.93u (US) and v1.93j (JP)" in row.inner_text()
        assert row.get_by_role("button").count() == 0
        assert "Your setup matches the tested setup." in page.locator(CHECK).inner_text()
        row.scroll_into_view_if_needed()
        shoot(page, tmp_path, f"setup-check-lab-rom-found-{width}.png")
        no_faults(seen)


def test_a_missing_rom_never_explains_unseen_ghosts(served, tmp_path):  # noqa: F811
    """The ROM is no Project64 setting: with only it flagged, unseen ghosts show no setup notice."""
    with opened(served, 850, stalled=True, check=check_with(tmp_path, held(tmp_path))) as (page, seen):
        page.wait_for_timeout(1200)
        assert page.locator(NOTICE).count() == 0
        no_faults(seen)

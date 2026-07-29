"""Every destination the shell declares must be reachable at every width.

The bug this exists for (user, 2026-07-28: "rank is missing from the more menu
at the bottom!!!!"): ui/app.js had `NAV_GROUPS` as the registry AND two
hand-written lists beside it -- MobileNav's three tabs and MobileMore's grid.
`Rank` was in the registry and in NEITHER list, so at every width at or below
760px, where the sidebar is gone, it could not be reached at all.  The
responsive sweep confirmed it at all 13 such widths.

Nobody copy-pasted anything.  Each list was written for its own layout by
someone who could see only that layout -- the same shape as the icon-art
divergence `tests/test_single_source.py` documents.

**Why this is not a row in test_single_source.py**, which is where it was tried
first: that file's scan exempts the OWNER file, and the second list lived
INSIDE app.js.  An owners-based scan structurally cannot see a duplicate in the
file that owns the concept -- the mutation proved it, passing green with
MobileNav's hardcoded array restored.  The check that works is arity: a nav
pair may appear EXACTLY ONCE in app.js, inside NAV_GROUPS.

This is the static half.  Reachability itself is not a scannable property, so
it pairs with the sweep's unreachable-tab probe (tools/responsive_probe.js),
which clicks around the real shell at 30 viewports and answers the question
this file cannot: can you actually get there.
"""
import re
from pathlib import Path

import pytest

from source_scan import code_only

APP_JS = (Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
          / "app.js")


def _source() -> str:
    return code_only(APP_JS, APP_JS.read_text(encoding="utf-8"))


def _nav_groups_block(source: str) -> str:
    match = re.search(r"const NAV_GROUPS = \[(.*?)\n\];", source, re.S)
    assert match, "NAV_GROUPS not found in app.js — did it get renamed?"
    return match.group(1)


def nav_pairs() -> list[tuple[str, str]]:
    return re.findall(r'\["([^"]+)",\s*"([^"]+)"\]', _nav_groups_block(_source()))


def test_the_registry_is_not_empty():
    """Control: every assertion below is vacuously true on an empty parse."""
    pairs = nav_pairs()
    assert len(pairs) >= 6, f"only parsed {pairs} out of NAV_GROUPS"
    assert ("Rank", "rank") in pairs


@pytest.mark.parametrize("name,icon", nav_pairs())
def test_no_destination_is_listed_twice(name, icon):
    """A nav pair appears once, in NAV_GROUPS. A second occurrence anywhere in
    app.js is a hand-written list growing back, which is exactly how Rank went
    missing -- and it is invisible to any owner-based scan because it lives in
    the owner file."""
    literal = f'["{name}", "{icon}"]'
    occurrences = _source().count(literal)
    assert occurrences == 1, (
        f'{literal} appears {occurrences} times in app.js. Destinations live '
        f'in NAV_GROUPS once; the narrow layouts derive from NAV_ITEMS '
        f'(MOBILE_BAR names what earns a permanent slot, and MobileMore is the '
        f'COMPLEMENT). A second list is how Rank became unreachable below '
        f'760px.')


def test_the_mobile_bar_names_only_real_destinations():
    """MOBILE_BAR is a RANKING, not a second list of what exists -- so every
    name in it must resolve, or it silently renders nothing."""
    source = _source()
    match = re.search(r"const MOBILE_BAR = \[(.*?)\];", source, re.S)
    assert match, "MOBILE_BAR not found in app.js"
    barred = re.findall(r'"([^"]+)"', match.group(1))
    assert barred, "MOBILE_BAR is empty"
    known = {name for name, _ in nav_pairs()}
    unknown = [name for name in barred if name not in known]
    assert not unknown, (
        f"MOBILE_BAR names {unknown}, which are not in NAV_GROUPS — they would "
        f"render as nothing at all in the bottom bar.")


def test_more_is_the_complement_so_nothing_can_go_missing():
    """The structural claim: MobileMore filters NAV_ITEMS by NOT-in-bar.

    Pinned as source shape rather than behaviour because a destination
    disappearing is not something a unit test can see -- the sweep's
    unreachable probe covers that half.  What this catches is the refactor
    that quietly turns the complement back into an enumeration.
    """
    source = _source()
    more = re.search(r"function MobileMore\(.*?\n}", source, re.S)
    assert more, "MobileMore not found in app.js"
    body = more.group(0)
    assert "NAV_ITEMS.filter" in body, (
        "MobileMore no longer derives its items from NAV_ITEMS. If it "
        "enumerates destinations again, a new NAV_GROUPS entry can go "
        "unreachable below 760px with nothing failing.")
    assert "inBar" in body, (
        "MobileMore no longer excludes the bottom-bar items by MOBILE_BAR, so "
        "the two lists can disagree about what is already on screen.")

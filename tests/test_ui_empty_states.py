# tests/test_ui_empty_states.py
"""The Practice tab's "nothing here yet" panels (ui/components/emptystate.js).

Four panels can legitimately have nothing to draw — attempt timeline,
performance trend, practice log, and the whole analysis card with no target
picked. Every one of them used to render an empty box inside a card with a
hard 458px height, which reads as a broken render rather than an honest state
(live report 2026-07-25: "like a 404 message").

Two classes of regression are worth a test. The first is the art contract: the
cast registry names PNG stems, and a renamed or deleted file turns into a
broken-image icon in the card the state exists to fix — that is a data fact
Python can check directly. The second is the wiring: each panel now chooses
between its real content and an empty state, and dropping either half of a
ternary restores the void silently, because nothing throws and no other test
renders these panels empty.
"""
import re
from pathlib import Path

from tests.source_scan import strip_comments

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "sm64_events" / "ui"
EMPTY_ART = UI / "assets" / "empty"
EMPTYSTATE_JS = UI / "components" / "emptystate.js"
PRACTICE_JS = UI / "components" / "practice.js"
ENTITYDETAIL_JS = UI / "components" / "entitydetail.js"
TIMELINE_JS = UI / "components" / "timeline.js"
INDEX_HTML = UI / "index.html"


def _cast() -> list[tuple[str, str]]:
    """(stem, name) for every entry in emptystate.js's CAST, comments out."""
    source = strip_comments(EMPTYSTATE_JS.read_text(encoding="utf-8"))
    block = re.search(r"export const CAST = \[(.*?)\n\];", source, re.S)
    assert block, "CAST not found in emptystate.js — did it move or get renamed?"
    entries = re.findall(r'stem:\s*"([^"]+)".*?name:\s*"([^"]+)"',
                         block.group(1), re.S)
    assert entries, "CAST parsed to nothing — the entry shape changed"
    return entries


def test_every_cast_member_has_its_png():
    """A stem with no file 404s into a broken-image icon, in the exact panel
    this component exists to make look deliberate."""
    for stem, _ in _cast():
        art = EMPTY_ART / f"{stem}.png"
        assert art.is_file(), f"CAST names {stem} but {art} does not exist"
        assert art.stat().st_size > 10_000, (
            f"{art} is {art.stat().st_size} bytes — too small to be the "
            "character art; a placeholder or a truncated copy")


def test_every_png_is_in_the_cast():
    """The other direction: art dropped into the folder and never wired up is
    dead weight in the exe and in every incremental update download."""
    on_disk = {p.stem for p in EMPTY_ART.glob("*.png")}
    named = {stem for stem, _ in _cast()}
    assert on_disk == named, (
        f"ui/assets/empty and CAST disagree — only on disk: "
        f"{sorted(on_disk - named)}; only in CAST: {sorted(named - on_disk)}")


def test_the_cast_has_more_than_one_character():
    """pickCast excludes the LAST NAME handed out so the two states that can
    share a screen never draw the same character. With one distinct name the
    exclusion would empty the pool and the picker would return undefined."""
    names = {name for _, name in _cast()}
    assert len(names) >= 2, f"pickCast needs 2+ distinct names, got {names}"


def test_pickcast_excludes_by_name_not_by_file():
    """Two of the six PNGs are Ukiki and two are Toad. Excluding the last
    STEM let ukiki_1 render beside ukiki_2, which still reads as the same
    monkey twice (caught in a headless render, 2026-07-25)."""
    source = strip_comments(EMPTYSTATE_JS.read_text(encoding="utf-8"))
    picker = re.search(r"export function pickCast\(\) \{(.*?)\n\}", source, re.S)
    assert picker, "pickCast not found in emptystate.js"
    assert "c.name !== lastName" in picker.group(1), (
        "pickCast must exclude the previous character by NAME; excluding by "
        "stem allows two different files of the same character side by side")


def _sections(source: str) -> dict[str, str]:
    """The bodies of the practice-card builders that own an empty panel."""
    bodies = {}
    for name in ("StarSection", "SegmentSection", "EmptyPractice"):
        match = re.search(rf"^function {name}\(.*?^}}", source, re.S | re.M)
        assert match, f"{name} not found in practice.js — did it get renamed?"
        bodies[name] = strip_comments(match.group(0))
    return bodies


def test_both_practice_cards_guard_the_trend_graph():
    """Progress returns "" for a section with no plottable points, so the
    caller has to ask the SAME question to put something there instead —
    hence the shared hasProgressPoints. A card that renders <Progress>
    unconditionally is back to an empty box.

    2026-08-03 (practice-log-entity-cards, task 4): the trend graph and its
    guard moved out of StarSection/SegmentSection into the shared
    EntityAnalysis (entitydetail.js), which both cards now render instead of
    building their own analysis block. So this checks both halves: each
    section renders the shared analysis card, and that shared card still
    asks hasProgressPoints before falling back to TrendEmpty -- either half
    slipping (a section stops calling it, or the shared card loses the
    guard) would be invisible to the other assertion alone."""
    bodies = _sections(PRACTICE_JS.read_text(encoding="utf-8"))
    for name in ("StarSection", "SegmentSection"):
        assert "<${EntityAnalysis}" in bodies[name], \
            f"{name} does not render the shared analysis card"
    analysis = strip_comments(ENTITYDETAIL_JS.read_text(encoding="utf-8"))
    body = re.search(r"^export function EntityAnalysis\(.*?^}", analysis, re.S | re.M)
    assert body, "EntityAnalysis not found in entitydetail.js — did it get renamed?"
    assert "hasProgressPoints(sec.progress" in body.group(0), (
        "EntityAnalysis renders the trend graph without asking whether it "
        "has any points — an empty section shows a blank block again")
    assert "<${TrendEmpty}" in body.group(0), \
        "EntityAnalysis has no empty state for the performance trend"


def test_both_practice_cards_have_an_empty_practice_log():
    bodies = _sections(PRACTICE_JS.read_text(encoding="utf-8"))
    for name in ("StarSection", "SegmentSection"):
        assert "<${AttemptLogEmpty}" in bodies[name], \
            f"{name} has no empty state for the practice log"
        assert "hasAttempts=${sec.attempts.length > 0}" in bodies[name], (
            f"{name} must tell AttemptLogEmpty whether attempts EXIST: "
            '"nothing recorded" and "everything is filtered out" are '
            "different states with different remedies")


def test_the_no_target_screen_explains_both_of_its_empty_cards():
    """EmptyPractice is the screen the user actually asked about — nothing
    selected, so both cards are empty at once."""
    body = _sections(PRACTICE_JS.read_text(encoding="utf-8"))["EmptyPractice"]
    assert body.count("<${EmptyState}") == 2, (
        "EmptyPractice must fill BOTH its analysis card and its unassigned "
        "log with an empty state; one of the two is back to a blank box")


def test_the_timeline_says_something_when_it_has_no_strip():
    """The timeline draws no strip at all with no points and no markers, and
    the `+ marker` chip alone does not explain what the block is for."""
    source = strip_comments(TIMELINE_JS.read_text(encoding="utf-8"))
    assert re.search(r"!showStrip\s*&&", source), (
        "timeline.js renders nothing when showStrip is false — the block is "
        "an unexplained gap above the marker chip")


def test_the_empty_state_cannot_grow_its_card():
    """.analysis-card/.attempts-card are a hard 458px for OBS stability. An
    empty state sized by its content would push past that and move the whole
    layout the moment the first attempt landed."""
    css = strip_comments(INDEX_HTML.read_text(encoding="utf-8"))
    rule = re.search(r"\.empty-state \{(.*?)\}", css, re.S)
    assert rule, ".empty-state rule not found in index.html"
    assert "min-height: 0" in rule.group(1), (
        ".empty-state must set min-height: 0 so it can shrink inside a "
        "fixed-height card instead of overflowing it")


def test_the_art_is_dimmed():
    """The user asked for it explicitly: at full strength a 128px Toad
    out-shouts the live rank banner one card over."""
    css = strip_comments(INDEX_HTML.read_text(encoding="utf-8"))
    rule = re.search(r"\.empty-art img \{(.*?)\}", css, re.S)
    assert rule, ".empty-art img rule not found in index.html"
    opacity = re.search(r"opacity:\s*([\d.]+)", rule.group(1))
    assert opacity and float(opacity.group(1)) <= 0.6, (
        "the empty-state art must stay dimmed; it is decoration sitting "
        "beside live practice data")

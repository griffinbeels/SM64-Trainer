"""Complete Sheet standards on the actual Practice and Library surfaces."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from find_uilab import find_uilab  # noqa: E402

if missing := find_uilab():
    pytest.skip(missing, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from ui_fixture import serve_ui_live, _run_coro  # noqa: E402
from sm64_events.core.events import Event  # noqa: E402
from standards_panel import api  # noqa: E402
from test_ui_standards_sync import _clear_fixture_startup_errors, _until  # noqa: E402

NAMES = ["Capless", "Toad", "Toadsworth", "Waluigi", "Wario", "Luigi", "Vanish", "Metal", "Mario"]


def _practice_target(base, service, label):
    target = next(t for group in api(base, "/api/library")["groups"]
                  for t in group["targets"] if t["label"].casefold() == label.casefold())
    detail = api(base, f"/api/library/target/{target['index']}")
    row = detail["approaches"][0]
    entity = row["entity_key"]
    assert entity.startswith("segment:")
    # Put the fixture at the segment's real location before selecting it;
    # a request from the default Whomp's Fortress stage must be refused.
    level = 16 if label == "Lakitu skip" else 6

    async def arrive():
        now = datetime.now(timezone.utc)
        await service.publish(Event(type="stage_changed", frame=10000,
            timestamp_utc=now, payload={"course_id": None, "level": level,
                                       "area": 1, "mode": None if level == 16 else "castle"}))
        await service.publish(Event(type="area_changed", frame=10000,
            timestamp_utc=now, payload={"level": level, "from": None, "to": 1}))

    _run_coro(arrive())
    api(base, "/api/target", {"kind": "segment", "segment_id": int(entity.split(":")[1])})
    api(base, "/api/import/manual", {"entity_key": entity, "strat_tag": row["strategy"],
                                    "time_cs": row["entries"][0]["time_cs"]})
    return entity


@pytest.mark.parametrize("width", [850, 1500])
@pytest.mark.parametrize("label", ["Lakitu skip", "JRB door - Enter JRB"])
def test_every_tier_and_division_is_visible_in_both_pages(width, label):
    with serve_ui_live() as (base, _service):
        entity = _practice_target(base, _service, label)
        card = f'.log-card[data-feed-key="{entity}"]'
        out = REPO / ".planning/sheet-complete-tiers/visuals"
        out.mkdir(parents=True, exist_ok=True)
        slug = "lakitu" if label == "Lakitu skip" else "jrb-door"
        with get_driver().launch(headless=True, viewport=(width, 1100)) as page:
            page.goto(base)
            page.wait_for(card)
            page.evaluate(f"""(() => {{
              const card = document.querySelector({json.dumps(card)});
              if (card.classList.contains('is-closed')) card.querySelector('.log-card-fold').click();
            }})()""")
            page.wait_for(card + " .standards-toggle")
            page.evaluate(f"document.querySelector({json.dumps(card + ' .standards-toggle')}).click()")
            page.wait_for(card + " .stdtable")
            page.evaluate(f"window.__rankCard = document.querySelector({json.dumps(card)})")
            names = page.evaluate("[...window.__rankCard.querySelectorAll('.std-tier-name')].map(e=>e.textContent.trim())")
            assert names == NAMES
            values = page.evaluate("[...window.__rankCard.querySelectorAll('.std-tier-name')].map(e => [...e.closest('tr').children].slice(1).map(c=>c.textContent.trim()))")
            assert all(cells and all(text and text != "—" for text in cells) for cells in values), values
            _clear_fixture_startup_errors(page, base)
            page.wait_ms(650)  # Let the panel's opening transition finish before capture.
            page.evaluate("window.__rankCard.scrollIntoView({block:'start'})")
            (out / f"{slug}-practice-{width}.png").write_bytes(page.screenshot())
            # Expand each real tier in turn; the panel intentionally opens one
            # tier at a time. All five subdivision rows must have a target.
            for index in range(9):
                page.evaluate(f"window.__rankCard.querySelectorAll('.std-tier-btn')[{index}].click()")
                _until(page, f"window.__rankCard.querySelectorAll('.std-tier-btn')[{index}].getAttribute('aria-expanded') === 'true'")
                page.wait_ms(450)
                divisions = page.evaluate("[...window.__rankCard.querySelectorAll('tr.std-sub')].filter(r=>r.getBoundingClientRect().height>1).map(r=>[...r.children].slice(1).map(c=>c.textContent.trim()))")
                assert len(divisions) == 5, (index, divisions)
                assert all(cells and all(text and text != "—" for text in cells) for cells in divisions), divisions
            (out / f"{slug}-mario-{width}.png").write_bytes(page.screenshot())
            page.evaluate("window.__rankCard.querySelector('.log-card-library-link').click()")
            page.wait_for(".library-target .library-section.open")
            section_names = page.evaluate("[...document.querySelectorAll('.library-section-name')].map(e=>e.textContent.trim())")
            for name in section_names:
                page.evaluate(f"""(() => {{
                  const name = [...document.querySelectorAll('.library-section-name')].find(e=>e.textContent.trim()==={json.dumps(name)});
                  if (!name.closest('.library-section').classList.contains('open')) name.click();
                }})()""")
                page.wait_ms(300)
                names = page.evaluate("[...document.querySelectorAll('.library-section.open .library-band-head > b')].map(e=>e.textContent.trim())")
                assert names == NAMES, (name, names)
                # Empty example groups render a labeled static row rather
                # than a disclosure button. Both forms carry standards.
                assert page.evaluate("document.querySelectorAll('.library-section.open .library-division').length") == 45
                brackets = page.evaluate("[...document.querySelectorAll('.library-section.open .library-division')].map(d=>d.querySelector('.meta')?.textContent.trim())")
                assert all(text and text != "—" for text in brackets), brackets
            (out / f"{slug}-library-{width}.png").write_bytes(page.screenshot())
            # Replay polls again during this longer tour. The offline fixture
            # omits that controller; account for only its exact paired 404s.
            _clear_fixture_startup_errors(page, base)
            assert page.problems() == []

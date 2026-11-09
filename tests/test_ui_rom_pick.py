"""The Ghost Lab's ROM step in a browser: the shipping component over the real route and store (fixture only).

Each case serves `rompick_fixture.py`: no app, emulator or real ROM. Images are 8 MiB of filler under a
Usamune header, made in the page itself.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from rompick_fixture import serve_rom_pick
from test_lab_roms import FakeProject64, rom, store_for

ROOT = Path(__file__).resolve().parents[1]

# A chosen file, built in the page: `name` at the header's name field, `country` its letter.
CHOOSE = """(() => {{
  const data = new Uint8Array({size});
  data.set([0x80, 0x37, 0x12, 0x40]);
  data.set(new TextEncoder().encode({name}.padEnd(20, " ")), 0x20);
  data.set(new TextEncoder().encode("NSM" + {country}), 0x3B);
  const transfer = new DataTransfer();
  transfer.items.add(new File([data], {file}));
  const input = document.querySelector(".rom-pick input[type=file]");
  input.files = transfer.files;
  input.dispatchEvent(new Event("change", {{ bubbles: true }}));
  return true;
}})()"""


def choose(page, name="SM64 USAMUNE v1.93u", country="E", size=8 << 20, file="practice.z64"):
    page.evaluate(CHOOSE.format(size=size, name=json.dumps(name), country=json.dumps(country), file=json.dumps(file)))


def driver():
    sys.path.insert(0, str(ROOT / "tools"))
    from find_uilab import find_uilab
    missing = find_uilab()
    if missing:
        pytest.skip(missing)
    from uilab.driver import get_driver
    return get_driver()


@pytest.fixture
def opened(request, tmp_path):
    options = getattr(request, "param", {})
    game = FakeProject64(None)
    game.running = False
    store = store_for(tmp_path, game=game, clock=time.monotonic)
    with serve_rom_pick(store, options.get("region", "us")) as base:
        with driver().launch(headless=True, viewport=(options.get("width", 850), 700)) as page:
            page.goto(base + "/")
            page.wait_for(".rom-pick")
            try:
                yield page, store, game, tmp_path
            finally:
                refusal = options.get("refusal", False)   # a refused pick answers 400, and the browser logs it
                problems = [p for p in page.problems() if not (refusal and ("/roms/pick" in p or "status of 400" in p))]
                assert problems == [], problems


def wait(page, expression, seconds=12):
    deadline = time.monotonic() + seconds
    while not page.evaluate(expression):
        assert time.monotonic() < deadline, expression
        page.wait_ms(50)


def state(page):
    return page.evaluate("document.querySelector('.rom-pick')?.dataset.state ?? null")


def geometry(page):
    return page.evaluate("""(() => { const card = document.querySelector('.rom-pick').getBoundingClientRect();
      return { viewport: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth,
               right: card.right, width: card.width, text: document.querySelector('.rom-pick').innerText }; })()""")


@pytest.mark.parametrize("opened", [{"width": 850}, {"width": 1400}], indirect=True)
def test_with_no_rom_the_step_names_the_exact_rom_and_offers_the_file(opened):
    page, _store, _game, directory = opened
    wait(page, "document.querySelector('.rom-pick')?.dataset.state === 'missing'")
    shape = geometry(page)
    assert "Super Mario 64 Usamune v1.93u" in shape["text"] and "Project64" in shape["text"]
    assert "never uploads" in shape["text"] and "Choose ROM file" in shape["text"]
    assert not shape["overflow"] and shape["right"] <= shape["viewport"]
    (directory / f"rom-pick-missing-{shape['viewport']}.png").write_bytes(page.screenshot())


@pytest.mark.parametrize("opened", [{"width": 850}], indirect=True)
def test_a_chosen_rom_is_kept_and_the_step_says_where_it_came_from(opened):
    page, store, _game, directory = opened
    wait(page, "document.querySelector('.rom-pick')?.dataset.state === 'missing'")
    choose(page)
    wait(page, "document.querySelector('.rom-pick')?.dataset.state === 'found'")
    text = geometry(page)["text"]
    assert "Super Mario 64 Usamune v1.93u found." in text and "the file you chose" in text
    copy = store.rom_for("us")
    assert copy is not None and copy.read_bytes()[0x20:0x33] == b"SM64 USAMUNE v1.93u"
    (directory / "rom-pick-found-850.png").write_bytes(page.screenshot())


@pytest.mark.parametrize("opened", [{"width": 850, "refusal": True}], indirect=True)
def test_a_wrong_file_is_refused_in_one_sentence_and_the_step_stays(opened):
    page, store, _game, _directory = opened
    wait(page, "document.querySelector('.rom-pick')?.dataset.state === 'missing'")
    choose(page, name="SUPER MARIO 64")
    wait(page, "Boolean(document.querySelector('.rom-pick [role=alert]'))")
    alert = page.evaluate("document.querySelector('.rom-pick [role=alert]').innerText")
    assert "This file is not Super Mario 64 Usamune v1.93u" in alert
    assert state(page) == "missing" and store.rom_for("us") is None


@pytest.mark.parametrize("opened", [{"width": 850}], indirect=True)
def test_the_other_regions_file_is_kept_and_the_step_still_asks(opened):
    page, store, _game, _directory = opened
    wait(page, "document.querySelector('.rom-pick')?.dataset.state === 'missing'")
    choose(page, name="SM64 USAMUNE v1.93j", country="J")
    wait(page, "Boolean(document.querySelector('.rom-pick [role=alert]'))")
    alert = page.evaluate("document.querySelector('.rom-pick [role=alert]').innerText")
    assert "Super Mario 64 Usamune v1.93j" in alert and "still needs Super Mario 64 Usamune v1.93u" in alert
    assert store.rom_for("jp") is not None and store.rom_for("us") is None


@pytest.mark.parametrize("opened", [{"width": 850}], indirect=True)
def test_starting_the_game_while_the_step_is_open_is_enough(opened):
    page, store, game, _directory = opened
    wait(page, "document.querySelector('.rom-pick')?.dataset.state === 'missing'")
    game.image, game.running = rom("us"), True
    wait(page, "document.querySelector('.rom-pick')?.dataset.state === 'found'")
    assert "copied it from the game running in Project64" in geometry(page)["text"]
    assert store.rom_for("us").read_bytes() == rom("us")

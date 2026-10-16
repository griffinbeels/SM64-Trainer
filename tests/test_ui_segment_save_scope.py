"""Shipping page -> scope API -> release input, in an offline HTTP fixture."""
import copy
import json
import os
import sys
from pathlib import Path

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.server import api
from sm64_events.tracking import service as service_module
from sm64_events.tracking.shared_defaults import SharedDefaultsStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver
from ui_fixture import serve_ui


def set_input(page, selector, value):
    page.evaluate(f"""const el = document.querySelector({json.dumps(selector)});
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(
        el, {json.dumps(value)});
      el.dispatchEvent(new Event('input', {{bubbles: true}}));""")


def capture_scope(page, tmp_path, can_share, width):
    captures = Path(os.environ.get("SM64_REPORT_DIR") or tmp_path)
    captures.mkdir(parents=True, exist_ok=True)
    shot = captures / f"segment-save-{'shared' if can_share else 'personal'}-{width}.png"
    shot.write_bytes(page.screenshot())
    print(f"Save scope screenshot: {shot}")


@pytest.mark.parametrize("can_share", [True, False])
def test_lblj_editor_saves_to_the_visible_scope(can_share, tmp_path, monkeypatch):
    source = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    root = tmp_path / "checkout"
    data = root / "src/sm64_events/data"
    data.mkdir(parents=True)
    seed_path = data / "defaults.seed.json"
    seed_path.write_text(json.dumps(source), encoding="utf-8")
    before = seed_path.read_bytes()
    monkeypatch.setattr(api, "shared_defaults_root", lambda: root if can_share else None)
    monkeypatch.setattr(service_module, "shared_defaults_root", lambda: root if can_share else None)
    monkeypatch.setattr(service_module, "SharedDefaultsStore", lambda checkout:
                        SharedDefaultsStore(checkout, base_builder=lambda: copy.deepcopy(source)))
    with serve_ui(reconcile_full_corpus=True) as base, get_driver().launch() as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for('button.nav-item[title="Segments"]')
        page.click('button.nav-item[title="Segments"]')
        page.wait_for(".segments-page .library-search")
        set_input(page, ".segments-page .library-search", "LBLJ")
        page.wait_for(".segment-row-main")
        page.evaluate("""const matches = Array.from(document.querySelectorAll('.segment-row-main'))
          .filter(row => row.querySelector('.segment-row-name')?.textContent.trim() === 'LBLJ');
          if (matches.length !== 1) throw new Error(`Expected one LBLJ, found ${matches.length}`);
          matches[0].click();""")
        page.wait_for(".segbuilder .builder-actions .primary-button")
        primary = ".segbuilder .builder-actions .primary-button"
        expected = "Save for everyone" if can_share else "Save segment"
        assert page.evaluate(f"document.querySelector('{primary}').textContent.trim()") == expected
        buttons = page.evaluate("Array.from(document.querySelectorAll('.builder-actions button'))"
                                ".map(b => b.textContent.trim())")
        assert ("Save only for me" in buttons) == can_share
        for width in (1500, 850):
            page.set_viewport(width, 1180)
            page.evaluate(f"document.querySelector('{primary}').scrollIntoView({{block:'center'}})")
            page.wait_ms(250)
            geometry = page.evaluate("Array.from(document.querySelectorAll('.builder-actions button'))"
                ".map(b => {const r=b.getBoundingClientRect(); return [r.left,r.right]})")
            assert all(0 <= left < right <= width for left, right in geometry)
            capture_scope(page, tmp_path, can_share, width)
        name = "LBLJ — corrected default through the visible save button"
        set_input(page, '.segbuilder input[placeholder="e.g. Lobby to BitDW"]', name)
        page.click(primary)
        # Persistence precedes the editor reload and confirmation. Wait for
        # the completed UI save before checking its database/release outputs.
        receipt = "Saved for the next release" if can_share else "Saved"
        page.wait_for(f'.app-notice[role="status"]:has-text("{receipt}")', timeout_ms=5000)
        assert receipt == page.evaluate(
            "document.querySelector('.app-notice[role=\"status\"]').textContent")
        # Read the DB-backed API after the actual button has completed its save.
        rows = page.evaluate("return fetch('/api/segments').then(r=>r.json())")
        row = next(r for r in rows if r.get("seed_key") == "seg:lblj")
        assert row["name"] == name
        assert row["seed_dirty"] == (0 if can_share else 1)
        if can_share:
            shipped = json.loads(seed_path.read_text(encoding="utf-8"))
            assert next(r for r in shipped["segments"] if r["seed_key"] == "seg:lblj")["name"] == name
            assert (data / "segments.authored.json").is_file()
        else:
            assert seed_path.read_bytes() == before
            assert not (data / "segments.authored.json").exists()
        assert page.problems() == []

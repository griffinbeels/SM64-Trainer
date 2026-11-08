"""Record in the Ghost Lab (M4 round 6, item 3; round 9: the Camera tab's Film row): the timeline the page
keeps and the Record row's flow, in jsdom (tests/frontend/scenerecord.test.js), and the row on the real page through the UI
fixture: Record, Stop, the video made by the real export child (over fake_viewer_host.py and the
"frames" encoder), and "Show in folder", at 850 and 1280 pixels. The export's own checks are
tests/test_scene_export.py; the video on the real renderer is the round's GPU check.
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
from frontend_runner import run_frontend
from test_ui_scene_viewer import camera_tab


def test_the_page_keeps_one_entry_per_display_frame_and_ends_at_show_in_folder():
    run_frontend("scenerecord.test.js")


# The Record row as drawn (in the Camera tab's Film row): its controls, whether it scrolls sideways, and the
# play bar's button height.
RECORD_ROW = """(() => {
  const row = document.querySelector('.scene-lab-record');
  const play = document.querySelector('.scene-lab-bar[aria-label=Playback] button').getBoundingClientRect();
  return { overflow: row.scrollWidth - row.clientWidth, text: row.textContent.replace(/\\s+/g, ' ').trim(),
    playHeight: Math.round(play.height * 100) / 100, buttons: [...row.querySelectorAll('button')].map((b) => ({
      name: b.textContent.trim(), height: Math.round(b.getBoundingClientRect().height * 100) / 100, disabled: b.disabled })),
    right: Math.round(row.getBoundingClientRect().right),
    card: Math.round(document.querySelector('.ghost-lab-tray').getBoundingClientRect().right) };
})()"""


def _row(page) -> dict:
    return page.evaluate(RECORD_ROW)


def _fits(row: dict) -> None:
    assert row["overflow"] <= 0 and row["right"] <= row["card"], row
    # The play bar's weight, to half a pixel (Stop, with its dot, is 34.5 px against 34, measured 2026-11-08).
    assert all(abs(button["height"] - row["playHeight"]) <= 0.5 for button in row["buttons"]), row


def _shoot(page, shots: Path, state: str) -> dict:
    """The Record row at 850 and 1280 pixels: its measures, and a picture of the tray."""
    rows = {}
    for width in (850, 1280):
        page.set_viewport_size({"width": width, "height": 1000})
        page.wait_for_timeout(80)
        rows[width] = _row(page)
        page.locator(".ghost-lab-tray").screenshot(path=str(shots / f"record-{state}-{width}.png"))
    return rows


def _slow_exports(tmp_path: Path):
    def extra(app, lab, seen):
        lab.exports.clips_dir, lab.exports.jobs_dir = tmp_path / "clips", tmp_path / "jobs"
        lab.exports.options = {"host": "fake", "fake_host": {"delay_ms": 60}, "encoder": {"kind": "frames"}}
    return extra


def test_record_then_stop_makes_the_video_and_shows_it_in_its_folder(tmp_path, monkeypatch):
    shots = tmp_path / "shots"
    shots.mkdir()
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_slow_exports(tmp_path)) as (base, _seen), \
            sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            errors, revealed = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # Show in folder must never open Explorer from a test: the reveal is answered here.
            page.route("**/api/replay/reveal", lambda route: (revealed.append(json.loads(route.request.post_data)),
                                                              route.fulfill(json={"ok": True})))
            page.goto(base)
            ready = page.locator(f"{attempt_scene.ROWS} .attempt-scene:not([disabled])").first
            ready.wait_for(timeout=20000)
            ready.click()
            page.locator(".scene-lab-record").wait_for(state="attached", timeout=20000)
            camera_tab(page)
            page.get_by_role("button", name="Record", exact=True).click()
            page.locator(".scene-lab-record-time", has_text="Recording 0:01").wait_for(timeout=5000)
            states = {"recording": _shoot(page, shots, "recording")}
            page.get_by_role("button", name="Stop", exact=True).click()
            page.locator(".scene-lab-record progress").wait_for(timeout=10000)
            states["making"] = _shoot(page, shots, "making")
            link = page.locator(".scene-lab-record-link")
            link.wait_for(timeout=60000)
            states["done"] = _shoot(page, shots, "done")
            link.click()
            page.wait_for_timeout(300)
            assert errors == []
        finally:
            browser.close()
    clips = list((tmp_path / "clips").glob("*.mp4"))
    assert len(clips) == 1 and revealed == [{"path": str(clips[0])}]
    made = json.loads(clips[0].read_text(encoding="utf-8"))
    assert made["fps"] == 60 and len(made["pictures"]) >= 60          # a second or more at 60 a second
    for widths in states.values():
        for row in widths.values():
            _fits(row)
    recording = states["recording"][850]
    assert [button["name"] for button in recording["buttons"]] == ["Stop"] and "Recording 0:01" in recording["text"]
    making = states["making"][1280]
    assert [button["name"] for button in making["buttons"]] == ["Record", "Cancel"]
    assert making["buttons"][0]["disabled"] is True                 # one video at a time
    done = states["done"][850]
    assert done["text"].startswith("RecordSaved synthetic ") and done["text"].endswith(
        ".mp4. This recording has no sound, so the video is silent. Show in folder"), done
    (tmp_path / "record-rows.json").write_text(json.dumps(states, indent=1), encoding="utf-8")

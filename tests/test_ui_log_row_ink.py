"""Round 12: visible lettering shares the row axis, including at browser zoom.

The earlier rectangle test passed while the screenshot's GOLD and small margin
looked low. Measure actual painted pixels as well as the boxes, using the real
PB-save path and the exact reported strings. No inferred font baseline is proof.
"""
import hashlib
import io
import json
import os
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR, FIXTURE_STRAT, _run_coro, serve_ui_live  # noqa: E402
from test_tracker_service import ev  # noqa: E402

CARD = ".log-card.log-card-active"
ROW = f"{CARD} .attempt-table tr.is-pb"
MEASURE = """selector => {
  const row=document.querySelector(selector), box=e=>{
    const r=e.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,mid:r.y+r.height/2};
  };
  return {row:box(row), texts:Object.fromEntries(Object.entries({
    time:'.attempt-result b',gold:'.pb-gold',save:'.pb-save.is-shown'
  }).map(([name,selector])=>{const e=row.querySelector(selector);
    return [name,{...box(e),text:e.textContent}];}))};
}"""


def _finish(service, anchor, frames):
    for event in (ev("practice_reset", anchor, {"igt_frames_before": 0}),
                  ev("star_collected", anchor + frames, {"course_id": FIXTURE_COURSE,
                     "star_id": FIXTURE_STAR, "igt_frames": frames, "igt_timed_at": "xcam"})):
        _run_coro(service.publish(event))
    return max(service.db.attempts(), key=lambda row: row.id).id


def _ink_centers(screenshot, measured):
    """Read bright glyph cores inside each actual text box, excluding its glow.

    The color predicates cover the time/save green and GOLD's warm lettering.
    Require a nonempty, multi-pixel glyph extent; missing samples never pass.
    Device scale is pinned to one, so DOM rectangles and PNG pixels share units.
    """
    image = Image.open(io.BytesIO(screenshot)).convert("RGB")
    centers = {}
    for name, box in measured["texts"].items():
        ys = []
        for y in range(max(0, int(box["y"]) - 3), min(image.height, int(box["y"] + box["height"]) + 4)):
            for x in range(max(0, int(box["x"])), min(image.width, int(box["x"] + box["width"]) + 1)):
                r, g, b = image.getpixel((x, y))
                seen = (r > 150 and g > 120 and b < 170 and r >= g * .9) if name == "gold" else (
                    g > 130 and g > r * 1.15 and g > b * 1.05)
                if seen:
                    ys.append(y)
        assert ys and max(ys) - min(ys) >= 4, (name, box, ys)
        centers[name] = (min(ys) + max(ys) + 1) / 2
    return centers


def test_time_gold_and_margin_ink_share_the_whole_row_center():
    output = Path(os.environ["SM64_ROW_INK_EVIDENCE"]) if os.environ.get("SM64_ROW_INK_EVIDENCE") else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    captures, errors, failures = [], [], []
    with serve_ui_live() as (base, service):
        # This pixel probe needs the exact 436 -> 397-frame margin. Explicitly
        # undo the fixture PB before establishing that historical baseline.
        previous = service.db.current_pb(FIXTURE_COURSE, FIXTURE_STAR, "igt", strat_tag=FIXTURE_STRAT)
        _run_coro(service.undo_pb(previous["attempt_id"], "igt"))
        old = _finish(service, 30000, 436)
        _run_coro(service.save_pb(old, "igt"))
        _finish(service, 31000, 397)
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                page = browser.new_page(viewport={"width": 850, "height": 1400}, device_scale_factor=1)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.on("response", lambda response: failures.append([response.url, response.status])
                        if "/api/" in response.url and response.status >= 400 else None)
                page.add_init_script("localStorage.setItem('scope','lifetime')")
                page.goto(base)
                page.locator(f"{CARD} .attempt-table tr").first.locator(".attempt-pb-slot button").click()
                page.locator(ROW + " .pb-save.is-shown").wait_for()
                page.wait_for_timeout(2300)
                asset = page.request.get(base + "/ui/components/attemptlog.js").body()
                assert asset == (ROOT / "src/sm64_events/ui/components/attemptlog.js").read_bytes()
                # A regional row keeps Lineup's disabled PB slot and explains
                # the JP/US gate added by main, rather than offering a save.
                reason = page.evaluate("""async () => (await import('/ui/components/pbreplay.js'))
                    .unavailableReason({outcome:'success',pb_blocked:{reason:'foreign_rom'}})""")
                assert reason == "Open this run's game version to save it as its PB"
                for width, zoom in ((850, 1), (1500, 1), (1500, 1.25), (1500, 1.5)):
                    page.set_viewport_size({"width": width, "height": 1400})
                    page.evaluate("z => document.documentElement.style.zoom=z", zoom)
                    page.locator(ROW).scroll_into_view_if_needed()
                    page.wait_for_timeout(100)
                    measured = page.evaluate(MEASURE, ROW)
                    assert [value["text"] for value in measured["texts"].values()] == [
                        '13"23', "GOLD!!", "-1.30 (39 frames)"]
                    png = page.screenshot()
                    centers = _ink_centers(png, measured)
                    capture = {"width": width, "zoom": zoom, "centers": centers, **measured,
                               "asset_sha256": hashlib.sha256(asset).hexdigest(), "launch_id": base}
                    captures.append(capture)
                    if output:
                        (output / f"row-{width}-{zoom}.png").write_bytes(png)
                        page.locator(ROW).screenshot(path=str(output / f"row-only-{width}-{zoom}.png"))
                        (output / "measurements.json").write_text(json.dumps(captures, indent=2), encoding="utf-8")
                    # One physical pixel permits antialiasing/hinting. The rejected
                    # 150% render put GOLD >2px and the margin >1px below the axis.
                    assert max(abs(mid - measured["row"]["mid"]) for mid in centers.values()) <= 1, capture
                    assert max(centers.values()) - min(centers.values()) <= 1, capture
                    # Secondary box check catches inline strut participation even
                    # when hinting happens to land every glyph on the same pixels.
                    boxes = [value["mid"] for value in measured["texts"].values()]
                    assert max(boxes) - min(boxes) < .2, capture
                assert not errors and not failures, (errors, failures)
            finally:
                browser.close()

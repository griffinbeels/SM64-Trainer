"""Actual drawer + actual fragment HTTP/service output; independently painted IDs."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from test_replay_fragment_service import fragment_service as fragment_service
from test_replay_fragment_archive import recording as recording
from test_replay_picture_identity import encoder as encoder
from test_ui_replay_picture_steps import PROJECT, STORY
from sm64_events.server.replay_api import create_replay_router


def test_native_fragment_drawer_seeks_real_picture_and_input(tmp_path, fragment_service):
    service = fragment_service
    app = FastAPI()
    app.include_router(create_replay_router(service))
    inputs = {"frames": 60, "attempt_frames": 60, "lead_frames": 0, "fps": 30,
              "stretches": [[0, 1000, 60]], "buttons": [[32768, "A"]], "stick_max": 84,
              "dead_zone": 8, "angle_units": 65536, "actions": [], "markers": [],
              "runs": [{"start": i, "length": 1, "buttons": 32768, "stick_x": 0,
                        "stick_y": i, "yaw": 0, "speed": 32} for i in range(60)]}
    with TestClient(app) as client, PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def replay(route):
                response = client.post("/api/attempts/42/replay")
                assert response.status_code == 200, response.text
                route.fulfill(json=response.json())
            def media(route):
                headers = route.request.headers
                response = client.get("/api/replay/clips/clip_attempt_42.mp4", headers={
                    key: headers[key] for key in ("range", "if-range") if key in headers})
                requests.append({"range": headers.get("range"), "status": response.status_code,
                                 "bytes": len(response.content)})
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
            page.route("**/api/attempts/*/replay", replay)
            page.route("**/api/replay/clips/*", media)
            page.route("**/api/attempts/*/inputs*", lambda route: route.fulfill(json=inputs))
            page.route("**/api/attempts/*/replay/review-state", lambda route: route.fulfill(json={
                "loop": None, "zoom": None, "template_offsets": {}}))
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            page.evaluate(STORY.setup)
            page.wait_for_selector(".input-inspector")
            video = page.locator(".attempt-drawer video")
            video.evaluate("v => v.pause()")
            page.get_by_role("button", name="Start", exact=True).click()
            page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('15 /')")
            read = """v => {const c=document.createElement('canvas');c.width=320;c.height=240;
              const x=c.getContext('2d',{willReadFrequently:true});x.drawImage(v,0,0,320,240);
              return Array.from({length:8},(_,i)=>x.getImageData(i*40+20,120,1,1).data[0]>128?1<<i:0).reduce((a,b)=>a+b,0);} """
            assert video.evaluate(read) == 15
            page.get_by_role("button", name="Forward 1", exact=True).click()
            page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('16 /')")
            assert video.evaluate(read) == 16
            video.evaluate("v => {v.currentTime = 1.01;}")
            page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('45 /')")
            assert video.evaluate(read) == 45
            assert video.evaluate("v => v.currentSrc.includes('/api/replay/clips/')")
            assert any(request["status"] == 206 for request in requests)
            assert not list(service.cfg.scratch_dir.rglob("*.mp4"))
            page.locator(".attempt-drawer").screenshot(path=str(tmp_path / "native-fragment-review.png"))
            (tmp_path / "browser-ranges.json").write_text(json.dumps(requests), encoding="utf-8")
            assert not errors
        finally:
            browser.close()

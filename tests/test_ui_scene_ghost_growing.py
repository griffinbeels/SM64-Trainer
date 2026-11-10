"""A run watched from a ghost row grows into the scene through the lab's real routes, on the real page (round 9; the
lead's fast-load contract, 2026-11-08).

POST /api/scene-lab/open {ghost} is the lab's own (server/scene_resim.py's progressive build); only its runner is a
stand-in, which writes the UI fixture's own recording in place, publishes its first frames and waits for the test.
Until the rest is written the row and the picture's foot show the share built, the slider spans the whole run with
the part not built yet as a striped stretch past the last frame built, and the old scene played until the first
frames came. Then either the build goes on, the "grown" messages fill the slider and the progress goes; or Stop ends
it through POST /open/cancel and the scene keeps the frames it has. At 850 and 1280 pixels.
"""
import os
import threading
from pathlib import Path

import pytest
from fastapi import APIRouter
from playwright.sync_api import sync_playwright

import test_scene_lab_viewer as viewer_tests
import test_ui_attempt_scene as attempt_scene
import test_ui_scene_ghosts as scene_ghosts
from sm64_events.server.scene_resim import ResimRefused

CLIP = b"SM64GHO7" + bytes(200)
ROW = "li[data-ref='top-all-3']"
BUILD = ".ghost-lab-play .ghost-lab-build"


class FixtureBuild:
    """ghost_resim's build as the fast load makes it (test_scene_lab_resim_api.GrowingRunner), over the UI fixture's
    own recording: its first `publish` frames written in place and published, the rest once `go` is set; a cancel
    ends it the way the child's end does."""

    def __init__(self, lab_folder: Path, publish: int = 2):
        self.pk = viewer_tests.synthetic.pk
        self.session, self.rom = lab_folder / "tap-synthetic", lab_folder / "rom.z64"
        self.out = lab_folder / "lab-root" / "resim" / "ghost-growing.sm64scene"
        self.publish, self.go, self.resim, self.calls, self.published = publish, threading.Event(), None, [], None
        self.total = sum(1 for _ in self.pk.load_source(self.session).captures) - 1   # the scene runs one behind

    def __call__(self, args, on_progress=None, exclusive=True):
        self.calls.append(args[0])
        if args[0] == "check-slot":
            return {"ok": True, "frames": 60, "compared": 60, "reason": None}
        source = self.pk.load_source(self.session)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        sidecar = self.out.with_suffix(".grow")
        writer = self.pk.PackageWriter(source, self.rom.read_bytes(), self.out, self.pk.ZLIB, in_place=True)
        captures = iter(source.captures)
        for _ in range(self.publish + 1):
            writer.add(next(captures))
        published = self.published = writer.publish(sidecar)       # the frames in the scene so far
        states = {"allowed": True, "reason": None, "until": None, "check": "load"}
        on_progress({"stage": "re-simulating", "done": published, "total": self.total, "package": str(self.out),
                     "sidecar": str(sidecar), "published": published, "start": "simulated_reset", "built_to": published,
                     "parting": None, "save_states": states})
        while not self.go.wait(0.05):
            if self.resim is not None and self.resim.building is not None and self.resim.building.cancelled:
                writer.abandon()
                raise ResimRefused("cancelled")
        for capture in captures:
            writer.add(capture)
        writer.finish()
        sidecar.unlink()
        return {"path": str(self.out), "info": {"start": "simulated_reset", "parting_frame": None, "first_record": 1,
                                                "exact": "mario", "records": self.total + 1, "save_states": states}}


def _rig(builds: dict):
    """test_ui_scene_ghosts.py's lists and stage, with the lab's own open for a ghost and the stand-in runner."""
    def rig(app, lab, seen):
        scene_ghosts._stubs(app, lab, seen)
        build = builds["build"] = FixtureBuild(Path(lab.config_path).parent)
        build.resim = lab.resim
        lab.ghost_loader = lambda ref: {"clip": CLIP, "label": ref, "target": None}
        lab.resim.runner = build
        lab.resim._starts_root = Path(lab.config_path).parent / "population"
        stub_open = next(route for route in app.router.routes if getattr(route, "path", "") == "/api/scene-lab/open")
        router = APIRouter()

        @router.post("/api/scene-lab/open")
        async def open_scene(body: dict):
            if "ghost" not in body:
                return await stub_open.endpoint(body)
            seen["opened"].append(body)
            return await lab.open(ghost=body["ghost"])        # the lab's own progressive open

        app.include_router(router)
        for _ in router.routes:
            app.router.routes.insert(0, app.router.routes.pop())
    return rig


def _keep_folder():
    """A person inspecting the build names a folder; the screenshots are not otherwise kept."""
    named = os.environ.get("SM64_GHOST_GROWING_SHOTS")
    return Path(named) if named else None


def _building(page, build: FixtureBuild) -> None:
    """The first frames are the scene: the row and the picture's foot say how much is built, the slider spans the
    whole run and is striped past the last frame built."""
    for _ in range(600):                                        # the build publishes its first frames
        if build.published is not None:
            break
        page.wait_for_timeout(50)
    share = round(100 * build.published / build.total)
    page.wait_for_function(f"document.querySelector({BUILD!r})?.getAttribute('aria-valuenow') === '{share}'", timeout=30000)
    assert f"Making {scene_ghosts.PLAYERS[3]}'s scene" in page.locator(BUILD).inner_text()
    assert page.locator(f"{ROW}.is-building .ghost-lab-build.is-compact").count() == 1
    assert page.locator(f"{ROW} > .ghost-lab-watch").get_attribute("aria-pressed") == "true"
    slider = page.locator('.ghost-lab-play input[aria-label="Frame"]')
    assert slider.get_attribute("max") == str(build.total - 1)
    reach = float(page.locator(".ghost-lab-unbuilt").get_attribute("data-reach"))
    assert reach == pytest.approx((build.published - 1) / (build.total - 1), abs=1e-3)
    track, stripes = (page.locator(part).bounding_box() for part in (".ghost-lab-track", ".ghost-lab-unbuilt"))
    assert stripes["x"] > track["x"] + 8 and abs((stripes["x"] + stripes["width"]) - (track["x"] + track["width"])) <= 1
    assert page.locator(".ghost-lab-play .scene-lab-frame").inner_text().startswith(f"Frame 1 of {build.total}")
    page.keyboard.press("End")                                   # as far as the build goes, not past it
    page.wait_for_function(f"document.querySelector('.ghost-lab-play .scene-lab-frame').textContent"
                           f".startsWith('Frame {build.published} of {build.total}')", timeout=10000)


@pytest.mark.parametrize("ending", ["finished", "stopped"])
def test_a_watched_ghost_plays_from_its_first_frames_while_the_lab_builds_the_rest(ending, tmp_path, monkeypatch):
    builds = {}
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_rig(builds)) as (base, seen), \
            sync_playwright() as play:
        scene_ghosts._answer_ghosts(monkeypatch, seen)
        browser, page, errors = scene_ghosts._browse(play, base)
        try:
            page.locator('.nav-item[title="Ghost Lab"]:visible').click()
            page.wait_for_function(scene_ghosts.DRAWN, timeout=20000)
            page.locator(f"{ROW} > .ghost-lab-watch").wait_for(timeout=20000)
            page.locator(f"{ROW} > .ghost-lab-watch").click()
            build = builds["build"]
            _building(page, build)
            keep = _keep_folder()
            for width in (850, 1280):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(200)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                if keep:
                    page.locator(".scene-lab").screenshot(path=str(keep / f"ghost-lab-growing-real-{width}.png"))
            if ending == "finished":
                build.go.set()
            else:
                page.get_by_role("button", name="Stop making the scene").click()
            page.wait_for_function("!document.querySelector('.ghost-lab-build') && !document.querySelector('.ghost-lab-unbuilt')",
                                   timeout=30000)
            frames = build.total if ending == "finished" else build.published
            words = page.locator(".ghost-lab-play .scene-lab-frame").inner_text()
            assert words.startswith("Frame ") and f" of {frames}" in words and "+" not in words, words
            assert page.locator('.ghost-lab-play input[aria-label="Frame"]').get_attribute("max") == str(frames - 1)
            assert page.locator(f"{ROW}.is-building").count() == 0
            assert page.locator(f"{ROW} > .ghost-lab-watch").get_attribute("aria-pressed") == "true"
            assert seen["opened"][-1] == {"ghost": "top-all-3"} and build.calls == ["build"]
            assert errors == []
        finally:
            browser.close()

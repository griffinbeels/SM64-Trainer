"""The Ghost Lab's ROM step served alone: the shipping component, the real route and a real store in a temp folder.

serve_rom_pick opens only an owned loopback fixture (the same thread lifecycle as tools/ui_fixture.py). The
store's Project64 is a fake (`test_lab_roms.FakeProject64`), so nothing reads a real process or ROM.
"""
from __future__ import annotations

import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from sm64_events.scenelab.roms import RomStore
from sm64_events.server.lab_roms_api import create_rom_router

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src/sm64_events/ui"


def fixture_html(region: str) -> str:
    source = (UI / "index.html").read_text(encoding="utf-8")
    styles = "\n".join(re.findall(r"<style>([\s\S]*?)</style>", source))
    imports = re.search(r'<script type="importmap">([\s\S]*?)</script>', source)[1]
    return ("<!doctype html><html><head><meta charset='utf-8'><title>ROM step isolated fixture</title>"
            + '<link rel="icon" href="/ui/favicon.png">'
            + '<script type="importmap">' + imports + "</script><style>" + styles + "</style></head><body>"
            + '<div class="app-shell"><aside class="app-sidebar"><div class="app-brand">SM64 Trainer</div></aside>'
            + '<main class="app-main"><h1>Ghost Lab</h1><div id="rom-pick-fixture"></div></main></div>'
            + '<script type="module">import {h,render} from "preact";'
            + 'import {RomPick} from "/ui/components/rompick.js";'
            + f'render(h(RomPick,{{region:"{region}"}}),document.querySelector("#rom-pick-fixture"));'
            + "</script></body></html>")


def create_rom_pick_app(store: RomStore, region: str) -> FastAPI:
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    def page():
        return fixture_html(region)

    @app.get("/api/local-access")
    def local_access():
        return {"request_token": "isolated-rom-pick-fixture"}

    app.include_router(create_rom_router(store))
    app.mount("/ui", StaticFiles(directory=UI), name="ui")
    return app


@contextmanager
def serve_rom_pick(store: RomStore, region: str = "us"):
    """Parent-owned browser gate only; closes its exact listener and thread."""
    sys.path.insert(0, str(ROOT / "tools"))
    import ui_fixture
    listener = ui_fixture._bound_socket()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_rom_pick_app(store, region), host="127.0.0.1", port=port,
                                           log_level="error", lifespan="off", ws="none",
                                           loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"))
    worker, errors = ui_fixture._fixture_server_thread(server, [listener])
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            raise RuntimeError("ROM step fixture did not start") from (errors[0] if errors else None)
        yield f"http://127.0.0.1:{port}"
    finally:
        ui_fixture._stop_fixture_server(server, worker, errors)
        listener.close()

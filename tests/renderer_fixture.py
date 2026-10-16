"""Pinned public renderer source for CPU witnesses, independent of machine setup.

A maintainer's existing archive is only a read-only seed. Every test worker
verifies its own temporary copy through the production archive fetcher; a clean
runner downloads the same hash-pinned public source, never private build libs.
"""
import importlib.util
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]


def renderer_tool():
    spec = importlib.util.spec_from_file_location("fixture_renderer_build", ROOT / "tools/build_renderer.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build


def renderer_source_archive(cache, build):
    pinned = build.inputs()["upstream"]
    filename = f"GLideN64-{pinned['commit']}.zip"
    cache.mkdir(parents=True, exist_ok=True)
    cached = build.default_cache() / filename
    if cached.is_file():
        shutil.copyfile(cached, cache / filename)
    return build.fetch_archive(cache, pinned)


@pytest.fixture(scope="session")
def renderer_archive(tmp_path_factory):
    return renderer_source_archive(tmp_path_factory.mktemp("pinned-renderer"), renderer_tool())

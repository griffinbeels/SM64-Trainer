"""A clean renderer cache runs CPU witnesses; wrong source bytes never pass."""
import hashlib
import io

import pytest

from renderer_fixture import renderer_source_archive, renderer_tool


def configured_renderer(tmp_path, monkeypatch, content=b"bounded upstream archive fixture"):
    build = renderer_tool()
    pinned = {"commit": "a" * 40, "url": "https://fixture.invalid/pinned.zip",
              "archive_sha256": hashlib.sha256(content).hexdigest()}
    monkeypatch.setattr(build, "inputs", lambda: {"upstream": pinned})
    machine_cache = tmp_path / "machine-cache"
    monkeypatch.setattr(build, "default_cache", lambda: machine_cache)
    return build, pinned, machine_cache


def test_clean_renderer_cache_fetches_and_verifies_the_exact_pinned_bytes(tmp_path, monkeypatch):
    content = b"bounded upstream archive fixture"
    build, pinned, machine = configured_renderer(tmp_path, monkeypatch, content)
    calls = []
    def fetch(url, *, timeout):
        calls.append((url, timeout))
        return io.BytesIO(content)
    monkeypatch.setattr(build.urllib.request, "urlopen", fetch)
    cache = tmp_path / "clean-test-cache"
    archive = renderer_source_archive(cache, build)
    assert not machine.exists()
    assert archive.parent == cache and archive.read_bytes() == content
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == pinned["archive_sha256"]
    assert calls == [(pinned["url"], 120)]
    assert renderer_source_archive(cache, build) == archive
    assert len(calls) == 1


def test_existing_verified_source_is_copied_without_touching_the_machine_cache(tmp_path, monkeypatch):
    build, pinned, machine = configured_renderer(tmp_path, monkeypatch)
    machine.mkdir()
    source = machine / f"GLideN64-{pinned['commit']}.zip"
    source.write_bytes(b"bounded upstream archive fixture")
    before = source.read_bytes(), source.stat().st_mtime_ns
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("cache hit downloaded"))
    archive = renderer_source_archive(tmp_path / "test-cache", build)
    assert archive != source and archive.read_bytes() == before[0]
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


@pytest.mark.parametrize("cached", [False, True])
def test_wrong_renderer_source_is_fatal_and_never_modifies_the_machine_copy(tmp_path, monkeypatch, cached):
    build, pinned, machine = configured_renderer(tmp_path, monkeypatch)
    source = machine / f"GLideN64-{pinned['commit']}.zip"
    if cached:
        machine.mkdir()
        source.write_bytes(b"wrong source bytes")
    monkeypatch.setattr(build.urllib.request, "urlopen", lambda *_a, **_k: io.BytesIO(b"wrong source bytes"))
    cache = tmp_path / "test-cache"
    with pytest.raises(ValueError, match="differs from the pinned"):
        renderer_source_archive(cache, build)
    assert not (cache / source.name).exists()
    if cached:
        assert source.read_bytes() == b"wrong source bytes"


def test_missing_local_audio_assets_use_the_existing_copyrighted_rom_skip_contract(tmp_path, monkeypatch):
    from skip_inventory import allowed_for
    from test_ghost_audio import test_ghost_sound_engine_queue_and_volume
    monkeypatch.delenv("SM64_MODEL_ROM", raising=False)
    monkeypatch.delenv("SM64_MODEL_SEED_DIR", raising=False)
    with pytest.raises(pytest.skip.Exception) as failure:
        test_ghost_sound_engine_queue_and_volume(tmp_path)
    assert allowed_for(str(failure.value))
    assert allowed_for("requires verified cached pinned renderer archive") is None

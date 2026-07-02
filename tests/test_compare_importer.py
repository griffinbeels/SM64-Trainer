import pytest
from sm64_events.compare.importer import VideoImporter
from sm64_events.tracking.comparisons import cache_name_for


def _importer(tmp_path, downloader=None, runner=None):
    cache = tmp_path / "cache"; cache.mkdir()
    calls = []

    def default_runner(cmd):
        # fake ffmpeg: just create the output file (last arg)
        calls.append(cmd)
        open(cmd[-1], "wb").write(b"normalized")

    return VideoImporter(cache, ffmpeg="ffmpeg",
                         downloader=downloader,
                         runner=runner or default_runner), cache, calls


def test_import_file_copies_and_normalizes(tmp_path):
    src = tmp_path / "clip.mp4"; src.write_bytes(b"raw")
    imp, cache, calls = _importer(tmp_path)
    name = imp.import_video("file", str(src))
    assert name == cache_name_for(str(src))
    assert (cache / name).exists()
    assert len(calls) == 1  # ffmpeg ran once


def test_import_dedup_skips_second_time(tmp_path):
    src = tmp_path / "clip.mp4"; src.write_bytes(b"raw")
    imp, cache, calls = _importer(tmp_path)
    imp.import_video("file", str(src))
    imp.import_video("file", str(src))  # already cached
    assert len(calls) == 1  # ffmpeg did NOT run again


def test_import_missing_file_raises_lookup(tmp_path):
    imp, _, _ = _importer(tmp_path)
    with pytest.raises(LookupError):
        imp.import_video("file", str(tmp_path / "nope.mp4"))


def test_import_youtube_uses_downloader_and_progress(tmp_path):
    got = []

    def fake_dl(ref, dest_dir):
        p = dest_dir / "dl.mp4"; p.write_bytes(b"yt"); return p

    imp, cache, calls = _importer(tmp_path, downloader=fake_dl)
    name = imp.import_video("youtube", "https://youtu.be/abc",
                            progress_cb=lambda f, m: got.append((f, m)))
    assert (cache / name).exists()
    assert got and got[-1][0] == 1.0  # completed progress reported


def test_unknown_source_kind_raises_value(tmp_path):
    imp, _, _ = _importer(tmp_path)
    with pytest.raises(ValueError):
        imp.import_video("magnet", "x")


def test_normalize_failure_leaves_no_cache_file(tmp_path):
    # Fix 1 regression: a failed normalize must never leave ANY file in the
    # cache dir — not the published name (dedup would trust it forever) and
    # not the partial temp. Simulate ffmpeg writing a truncated file then dying.
    src = tmp_path / "clip.mp4"; src.write_bytes(b"raw")

    def dying_runner(cmd):
        open(cmd[-1], "wb").write(b"partial")   # ffmpeg wrote some bytes...
        raise RuntimeError("ffmpeg died mid-encode")  # ...then crashed

    imp, cache, _ = _importer(tmp_path, runner=dying_runner)
    name = cache_name_for(str(src))
    with pytest.raises(RuntimeError):
        imp.import_video("file", str(src))
    assert not imp.cache_path(name).exists()    # no valid cache hit for dedup
    assert not (cache / name).exists()
    assert list(cache.iterdir()) == []          # not even a leftover .tmp- temp

"""Every release attaches the complete third-party source of what it ships.

The GNU GPL asks that the source come from the same place as the binary
(GPL v2 section 3; GPL v3 section 6). licenses/third-party-sources.json pins
every third-party source archive; tools/corresponding_source.py writes them
into byte-stable archives, and tools/release.py attaches those to every
release beside its six product assets. Griffin chose that over one shared
upload (2026-11-08): "Attach to each release, that's probably what's easiest
for the user. They should just have to download the program once and be able
to update easily from the client forever." These tests use local files, never
the network.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from sm64_events.core import release_contract
from sm64_events.core.release_contract import REQUIRED_ASSETS, select_release

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import corresponding_source as sources  # noqa: E402
import release  # noqa: E402


def _manifest(tmp_path, files: dict[str, bytes], *, wrong: str | None = None) -> Path:
    items = []
    for name, data in files.items():
        path = tmp_path / "upstream" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        digest = hashlib.sha256(b"other bytes" if name == wrong else data).hexdigest()
        items.append({"url": path.as_uri(), "sha256": digest, "license": "MIT", "used_in": ["ffmpeg.exe"]})
    manifest = tmp_path / "third-party-sources.json"
    manifest.write_text(json.dumps({"about": "test", "user_agent": "test", "archives": {
        "SM64Trainer-third-party-source-test.tar": {"about": "test set", "sources": items}}}), encoding="utf-8")
    return manifest


def test_the_archives_are_byte_stable_and_carry_their_sidecars(tmp_path):
    manifest = _manifest(tmp_path, {"b-1.0.tar.gz": b"b" * 700, "a-2.0.tar.xz": b"a" * 300})
    first = sources.write_archives(tmp_path / "one", cache=tmp_path / "cache", manifest=manifest)
    second = sources.write_archives(tmp_path / "two", cache=tmp_path / "cache-2", manifest=manifest)
    assert [p.name for p in first] == sources.asset_names(manifest)
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]
    archive, sidecar = first
    assert sidecar.read_text(encoding="ascii").split() == [hashlib.sha256(archive.read_bytes()).hexdigest(),
                                                           archive.name]
    import tarfile
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        assert names == sorted(names)
        assert {m.mtime for m in tar.getmembers()} == {sources.FIXED_MTIME}
        assert tar.extractfile("SM64Trainer-third-party-source-test/a-2.0.tar.xz").read() == b"a" * 300
        assert b"a-2.0.tar.xz" in tar.extractfile("SM64Trainer-third-party-source-test/README.txt").read()


def test_a_source_that_is_not_the_pinned_file_stops_the_archive(tmp_path):
    manifest = _manifest(tmp_path, {"good.tar.gz": b"good", "changed.tar.gz": b"changed"}, wrong="changed.tar.gz")
    with pytest.raises(ValueError, match="not the pinned file"):
        sources.write_archives(tmp_path / "out", cache=tmp_path / "cache", manifest=manifest)
    assert not (tmp_path / "out" / "SM64Trainer-third-party-source-test.tar").exists()


def test_every_release_uploads_the_source_archives_beside_the_six():
    product = [p.name for p in release.release_assets(Path("dist"))]
    everything = [p.name for p in release.candidate_assets(Path("dist"))]
    assert everything[:6] == product and len(product) == 6
    assert everything[6:] == sources.asset_names()
    assert all(re.fullmatch(r"SM64Trainer-third-party-source-[a-z0-9-]+\.tar(\.sha256)?", name)
               for name in everything[6:])


def test_a_release_carrying_the_source_archives_still_updates_the_app():
    """The updater and the installer read only the app's files. The source
    archives beside them neither block an update nor join one, so a user
    downloads the program once and updates from inside it ever after."""
    names = [p.name for p in release.candidate_assets(Path("dist"))]
    page = {"tag_name": "1.10.0", "prerelease": False,
            "assets": [{"name": name, "browser_download_url": "https://example.com/" + name}
                       for name in names]}
    assert select_release([page], "1.9.1") == page
    assert sorted(release_contract.release_assets(page)) == sorted(REQUIRED_ASSETS)


def _pinned_digests() -> set[str]:
    spec = sources.load()
    return {item["sha256"] for archive in spec["archives"].values() for item in archive["sources"]}


def test_every_pin_is_complete():
    for archive in sources.load()["archives"].values():
        for item in archive["sources"]:
            assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), item
            assert item["url"].startswith(("https://", "http://deb.debian.org/")), item
            assert item["license"] and item["used_in"], item


def _words(text: str) -> str:
    return " ".join(text.split())


def test_what_is_not_attached_is_a_system_library_with_its_notices_shipped():
    """Lead decision 2026-11-08: the compiler's runtime (GCC's, winpthreads,
    the MinGW-w64 C runtime) leaves the mirror. The reason is quoted from the
    license texts themselves, so each quote must be in the tracked text it
    names, and each package's notices must ship."""
    for archive in sources.load()["archives"].values():
        skipped = archive.get("not_attached")
        if not skipped:
            continue
        for title, quotes in skipped["quotes"].items():
            text = _words((ROOT / re.search(r"\((licenses/[^)]+)\)", title)[1]).read_text(encoding="utf-8"))
            for quote in quotes:
                assert _words(quote) in text, (title, quote[:60])
        attached = {item["sha256"] for item in archive["sources"]}
        for item in skipped["sources"]:
            assert item["sha256"] not in attached
            assert re.search(r"/mingw-w64-(gcc|winpthreads)-", item["url"]), "only the compiler's runtime"
            for notice in item["notices"]:
                assert (ROOT / notice).is_file(), notice


def test_every_source_the_shipped_binaries_are_built_from_is_attached():
    """Drift guard: a pinned build input that is not in the attached archives
    would ship a binary whose source no release offers."""
    pinned = _pinned_digests()
    ffmpeg = json.loads((ROOT / "ffmpeg" / "build-inputs.json").read_text(encoding="utf-8"))
    for name, item in ffmpeg["sources"].items():
        assert item["sha256"] in pinned, f"ffmpeg.exe source {name} is not attached"
    renderer = json.loads((ROOT / "renderer" / "build-inputs.json").read_text(encoding="utf-8"))
    assert renderer["upstream"]["archive_sha256"] in pinned, "the GLideN64 tree is not attached"
    assert renderer["qt"]["sha256"] in pinned, "the Qt source is not attached"
    for name, item in renderer["libraries"].items():
        assert item["sha256"] in pinned, f"the plugin's {name} source is not attached"

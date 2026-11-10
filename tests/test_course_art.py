"""Course portraits are cut from the player's own ROM at run time; the Trainer ships none.

Every ROM here is SYNTHETIC: texture bytes this file makes up, packed into MIO0 blocks it encodes itself, with
recipes whose digests it computes from those bytes. No test reads, carries or pins a byte of a real ROM's
pictures (the shipped digests in course_art.PORTRAITS are fingerprints measured on his ROMs, and only the
real ROM can satisfy them).
"""
from __future__ import annotations

import hashlib
import io
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sm64_events import course_art as ca
from sm64_events.course_art import CourseArt, Portrait, Tile

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"


# -- a synthetic cartridge -----------------------------------------------------------------------------------------

def mio0_encode(data: bytes) -> bytes:
    """A real MIO0 stream: a run of one byte becomes a back-reference of distance 1 (an overlapping copy), a
    repeat of the 8 bytes before becomes one of distance 8 (a plain copy); everything else is a literal."""
    flags: list[int] = []
    comp = bytearray()
    raw = bytearray()
    i = 0
    while i < len(data):
        if i >= 8 and data[i:i + 8] == data[i - 8:i] and len(data) - i >= 8:
            comp += ((8 - 3) << 12 | (8 - 1)).to_bytes(2, "big")
            flags.append(0)
            i += 8
        elif i >= 1 and len(data) - i >= 6 and data[i:i + 6] == bytes([data[i - 1]]) * 6:
            comp += ((6 - 3) << 12 | (1 - 1)).to_bytes(2, "big")
            flags.append(0)
            i += 6
        else:
            raw.append(data[i])
            flags.append(1)
            i += 1
    while len(flags) % 32:
        flags.append(0)
    words = bytearray()
    for w in range(0, len(flags), 32):
        value = 0
        for bit in flags[w:w + 32]:
            value = value << 1 | bit
        words += value.to_bytes(4, "big")
    comp_at = 16 + len(words)
    raw_at = comp_at + len(comp)
    return b"MIO0" + len(data).to_bytes(4, "big") + comp_at.to_bytes(4, "big") + raw_at.to_bytes(4, "big") \
        + bytes(words) + bytes(comp) + bytes(raw)


def texture(seed: int, width: int, height: int) -> bytes:
    """RGBA16 pixels that differ by seed and position, every pixel opaque."""
    words = bytearray()
    for y in range(height):
        for x in range(width):
            r, g, b = (x + seed) % 32, (y * 3 + seed) % 32, (x ^ y ^ seed) % 32
            words += (r << 11 | g << 6 | b << 1 | 1).to_bytes(2, "big")
    return bytes(words)


def rgba(word: int) -> tuple[int, int, int, int]:
    return (((word >> 11) & 31) * 255 // 31, ((word >> 6) & 31) * 255 // 31, ((word >> 1) & 31) * 255 // 31,
            (word & 1) * 255)


def block_with(tiles: dict[int, bytes], size: int) -> bytes:
    data = bytearray(size)
    for offset, pixels in tiles.items():
        data[offset:offset + len(pixels)] = pixels
    return bytes(data)


def cartridge(*blocks: bytes, filler: int = 0x1000) -> tuple[bytes, list[int]]:
    rom, offsets = bytearray(b"\x80\x37\x12\x40" + bytes(filler - 4)), []
    for block in blocks:
        offsets.append(len(rom))
        rom += mio0_encode(block)
        rom += bytes(-len(rom) % 16 + 16)
    return bytes(rom), offsets


def recipe(stem: str, block_name: str, size: int, tiles: tuple[Tile, ...], data: bytes) -> Portrait:
    raw = b"".join(data[t.offset:t.offset + t.width * t.height * 2] for t in tiles)
    return Portrait(stem, stem.upper(), block_name, size, tiles, frozenset({hashlib.sha256(raw).hexdigest()}))


@pytest.fixture
def painted(monkeypatch):
    """A cartridge holding one painting and one mirrored quarter, in a block of a hinted size."""
    top, bottom, quarter = texture(1, 64, 32), texture(9, 64, 32), texture(17, 32, 32)
    data = block_with({0x100: top, 0x1100: bottom, 0x2100: quarter}, 0x3000)
    monkeypatch.setitem(ca.BLOCKS, "castle", frozenset({len(data)}))
    portraits = (recipe("bob", "castle", 64, ca._painting(0x100, 0x1100), data),
                 recipe("lll", "castle", 64, ca._mirrored_quarter(0x2100), data))
    rom, _ = cartridge(bytes(range(256)) * 40, data)
    return rom, portraits, (top, bottom, quarter)


# -- decoding ------------------------------------------------------------------------------------------------------

def test_mio0_decodes_literals_and_both_kinds_of_back_reference():
    data = b"abc" + b"z" * 40 + b"0123456789ABCDEF" * 4 + bytes(range(200))
    stream = mio0_encode(data)
    assert ca.mio0(stream, 0) == data
    assert ca.mio0(stream, 0, limit=50) == data[:50]
    # the stream really exercised both copies, or this test proves only literals
    assert any(stream[16 + 4 * k] != 0xFF for k in range(4))


def test_a_malformed_block_is_refused_rather_than_decoded_into_garbage():
    with pytest.raises(ValueError):
        ca.mio0(b"MIO1" + bytes(12), 0)
    reference_too_far = b"MIO0" + (4).to_bytes(4, "big") + (20).to_bytes(4, "big") + (22).to_bytes(4, "big") \
        + bytes(4) + (0x0005).to_bytes(2, "big")
    with pytest.raises(ValueError):
        ca.mio0(reference_too_far, 0)
    with pytest.raises(ValueError):
        ca.mio0(mio0_encode(bytes(range(100)))[:40], 0)


# -- cutting -------------------------------------------------------------------------------------------------------

def test_a_painting_is_its_top_half_over_its_bottom_half_at_full_size(painted):
    rom, portraits, (top, bottom, _) = painted
    pictures = ca.extract(rom, portraits)
    assert set(pictures) == {"bob", "lll"}
    image = Image.open(io.BytesIO(pictures["bob"])).convert("RGBA")
    assert image.size == (ca.OUTPUT_PIXELS, ca.OUTPUT_PIXELS)
    scale = ca.OUTPUT_PIXELS // 64
    word = lambda pixels, x, y, w=64: int.from_bytes(pixels[(y * w + x) * 2:(y * w + x) * 2 + 2], "big")
    for x, y in ((0, 0), (63, 0), (10, 31)):
        assert image.getpixel((x * scale, y * scale)) == rgba(word(top, x, y))
    for x, y in ((0, 0), (63, 31), (40, 5)):
        assert image.getpixel((x * scale + scale - 1, (32 + y) * scale)) == rgba(word(bottom, x, y))


def test_a_mirrored_quarter_fills_all_four_corners(painted):
    rom, portraits, (_, _, quarter) = painted
    image = Image.open(io.BytesIO(ca.extract(rom, portraits)["lll"])).convert("RGBA")
    scale = ca.OUTPUT_PIXELS // 64
    corner = rgba(int.from_bytes(quarter[:2], "big"))
    for x, y in ((0, 0), (63, 0), (0, 63), (63, 63)):
        assert image.getpixel((x * scale, y * scale)) == corner


def test_bytes_that_do_not_match_the_known_picture_give_no_picture(painted):
    rom, portraits, _ = painted
    wrong = Portrait("bob", "BOB", "castle", 64, portraits[0].tiles, frozenset({"0" * 64}))
    assert ca.extract(rom, (wrong,)) == {}


def test_a_block_of_an_unexpected_size_is_still_found_by_its_content(painted, monkeypatch):
    rom, portraits, _ = painted
    monkeypatch.setitem(ca.BLOCKS, "castle", frozenset({0x12345}))
    assert set(ca.extract(rom, portraits)) == {"bob", "lll"}


def test_every_shipped_recipe_fits_its_square_and_meets_the_grid_size():
    """Portraits draw in ~104px picker cells and up to ~174px in the banner, so a 3x screen asks for ~520px;
    ttc and rr once shipped as 64-100px thumbnails that blurred beside their neighbours (audit 2026-07-25)."""
    assert len({p.stem for p in ca.PORTRAITS}) == len(ca.PORTRAITS)
    for portrait in ca.PORTRAITS:
        assert portrait.digests, portrait.stem
        assert all(len(d) == 64 for d in portrait.digests)
        assert portrait.block in ca.BLOCKS
        for tile in portrait.tiles:
            assert 0 <= tile.x and tile.x + tile.width <= portrait.size, portrait.stem
            assert 0 <= tile.y and tile.y + tile.height <= portrait.size, portrait.stem
        covered = sum(t.width * t.height for t in portrait.tiles)
        assert covered == portrait.size ** 2, f"{portrait.stem} leaves holes or overlaps"
        assert ca.OUTPUT_PIXELS // portrait.size * portrait.size >= 256, portrait.stem


# -- the cache -----------------------------------------------------------------------------------------------------

def test_no_rom_lists_a_stand_in_for_every_portrait(tmp_path):
    art = CourseArt(tmp_path / "art", rom_for=lambda region: None)
    assert art.refresh() is False
    listing = art.listing()
    assert listing["from_rom"] is False
    assert listing["courses"] == {p.stem: f"standin/{p.stem}.svg" for p in ca.PORTRAITS} | SHIPPED_TAILS
    assert not (tmp_path / "art").exists()


def test_a_found_rom_is_cut_once_and_cached_by_its_hash(tmp_path, painted, monkeypatch):
    rom, portraits, _ = painted
    rom_path = tmp_path / "us.z64"
    rom_path.write_bytes(rom)
    asked = []
    art = CourseArt(tmp_path / "art", rom_for=lambda region: asked.append(region) or
                    (rom_path if region == "us" else None), portraits=portraits)
    assert art.refresh() is True
    key = f"{hashlib.sha256(rom).hexdigest()[:16]}-v{ca.FORMAT_VERSION}"
    assert art.listing() == {"courses": {"bob": f"{key}/bob.png", "lll": f"{key}/lll.png", **SHIPPED_TAILS},
                             "from_rom": True, "region": "us"}
    assert asked == ["us"]
    assert art.refresh() is False                      # same file: nothing to do
    assert sorted(p.name for p in (tmp_path / "art" / key).iterdir()) == ["bob.png", "lll.png", "portraits.json"]
    # Only the cache folder was written: nothing beside it, nothing in the ROM's folder.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["art", "us.z64"]
    assert sorted(p.name for p in (tmp_path / "art").iterdir()) == [key]

    monkeypatch.setattr(ca, "extract", lambda *a, **k: pytest.fail("a cached ROM was cut again"))
    again = CourseArt(tmp_path / "art", rom_for=lambda region: rom_path, portraits=portraits)
    assert again.refresh() is True
    assert again.listing()["courses"]["bob"] == f"{key}/bob.png"


def test_a_portrait_the_rom_cannot_supply_keeps_its_stand_in(tmp_path, painted):
    rom, portraits, _ = painted
    rom_path = tmp_path / "jp.z64"
    rom_path.write_bytes(rom)
    missing = Portrait("wdw", "WDW", "castle", 64, ca._painting(0x100, 0x1100), frozenset({"f" * 64}))
    art = CourseArt(tmp_path / "art", rom_for=lambda region: rom_path if region == "jp" else None,
                    portraits=portraits + (missing,))
    art.refresh()
    listing = art.listing()
    assert listing["region"] == "jp"
    assert listing["courses"]["wdw"] == "standin/wdw.svg"
    assert listing["courses"]["bob"].endswith("/bob.png")


def test_another_rom_replaces_the_old_rom_folder(tmp_path, painted):
    rom, portraits, _ = painted
    rom_path = tmp_path / "us.z64"
    rom_path.write_bytes(rom)
    art = CourseArt(tmp_path / "art", rom_for=lambda region: rom_path, portraits=portraits)
    art.refresh()
    first = art.listing()["courses"]["bob"].split("/")[0]
    rom_path.write_bytes(rom + b"\x00" * 16)
    assert art.refresh() is True
    second = art.listing()["courses"]["bob"].split("/")[0]
    assert second != first
    assert [p.name for p in (tmp_path / "art").iterdir()] == [second]


def test_a_failing_rom_store_reads_as_no_rom(tmp_path):
    def broken(region):
        raise OSError("the store's folder is gone")
    art = CourseArt(tmp_path / "art", rom_for=broken)
    assert art.refresh() is False
    assert art.listing()["from_rom"] is False


def test_only_a_cached_picture_name_can_be_served(tmp_path, painted):
    rom, portraits, _ = painted
    rom_path = tmp_path / "us.z64"
    rom_path.write_bytes(rom)
    art = CourseArt(tmp_path / "art", rom_for=lambda region: rom_path, portraits=portraits)
    art.refresh()
    key = art.listing()["courses"]["bob"].split("/")[0]
    assert art.file(key, "bob.png") is not None
    for bad_key, bad_name in ((key, "../us.z64"), (key, "portraits.json"), ("..", "bob.png"),
                              (key, "wdw.png"), ("standin", "bob.png"), (key, "BOB.PNG")):
        assert art.file(bad_key, bad_name) is None, (bad_key, bad_name)


def test_the_pictures_come_from_the_rom_store(monkeypatch, tmp_path):
    """The default ROM lookup is interface I3 (sm64_events/scenelab/roms.py), asked per region."""
    from sm64_events.scenelab import roms
    asked = []
    monkeypatch.setattr(roms, "rom_for", lambda region: asked.append(region) or (tmp_path if region == "us" else None))
    assert ca.default_rom_for("us") == tmp_path and ca.default_rom_for("jp") is None and asked == ["us", "jp"]


# -- the stand-in --------------------------------------------------------------------------------------------------

def test_the_stand_in_names_the_course_in_the_pages_own_colours():
    css = (UI / "index.html").read_text(encoding="utf-8")
    root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
    for token, value in ca.STANDIN_COLOURS.items():
        assert re.search(rf"{re.escape(token)}:\s*{re.escape(value)}\s*;", root), token
    for portrait in ca.PORTRAITS:
        svg = ca.standin_svg(portrait.stem)
        tree = ET.fromstring(svg)
        assert tree.get("viewBox") == "0 0 128 128"
        texts = [e.text for e in tree.iter("{http://www.w3.org/2000/svg}text")]
        assert texts == [portrait.label]
        assert not re.search(r"<image|href=|url\(", svg), "a stand-in draws nothing but its own shapes"
    assert ca.standin_svg("hmc") is None


# -- over HTTP -----------------------------------------------------------------------------------------------------

class Collected:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)
        return len(self.events)


def client_for(art, broadcaster=None):
    from fastapi import FastAPI
    from sm64_events.server.course_art_api import create_course_art_router
    app = FastAPI()
    app.include_router(create_course_art_router(art, broadcaster))
    return TestClient(app)


def test_without_course_art_the_server_serves_stand_ins_and_never_looks_for_a_rom():
    with client_for(None) as client:
        body = client.get("/api/icons/courses").json()
        assert body["from_rom"] is False
        for stem, tail in body["courses"].items():
            response = client.get(f"/api/icons/course/{tail}")
            assert response.status_code == 200, stem
            kind = "image/webp" if stem in ca.SHIPPED else "image/svg+xml"
            assert response.headers["content-type"].startswith(kind), stem
        assert set(body["courses"]) >= set(ca.SHIPPED)
        for bad in ("shipped/bob.webp", "shipped/..%2Fapp.js", "shipped/rr.png"):
            assert client.get(f"/api/icons/course/{bad}").status_code == 404, bad
        assert client.get("/api/icons/course/standin/hmc.svg").status_code == 404
        assert client.get("/api/icons/course/0123456789abcdef-v1/bob.png").status_code == 404


def test_the_first_listing_starts_the_watch_and_the_pictures_are_announced(tmp_path, painted):
    rom, portraits, _ = painted
    rom_path = tmp_path / "us.z64"
    rom_path.write_bytes(rom)
    art = CourseArt(tmp_path / "art", rom_for=lambda region: rom_path, portraits=portraits, watch_seconds=60)
    collected = Collected()
    try:
        with client_for(art, collected) as client:
            first = client.get("/api/icons/courses").json()
            assert first["from_rom"] is False or first["courses"]["bob"].endswith(".png")
            deadline = time.monotonic() + 10
            while not collected.events and time.monotonic() < deadline:
                time.sleep(0.01)
            assert [e.type for e in collected.events] == ["course_art_changed"]
            assert collected.events[0].payload["from_rom"] is True
            body = client.get("/api/icons/courses").json()
            assert body["from_rom"] is True
            picture = client.get(f"/api/icons/course/{body['courses']['bob']}")
            assert picture.status_code == 200
            assert picture.headers["content-type"] == "image/png"
            assert "immutable" in picture.headers["cache-control"]
            assert Image.open(io.BytesIO(picture.content)).size == (ca.OUTPUT_PIXELS, ca.OUTPUT_PIXELS)
    finally:
        art.stop()
    assert not art._thread.is_alive()


def test_the_real_app_mounts_the_portrait_routes(tmp_path):
    from import_fixture import OfflineMemory
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService
    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    service = TrackerService(db, broadcaster)
    app = create_app(Poller(OfflineMemory(), [], service), broadcaster, service=service)
    with TestClient(app) as client:
        tail = client.get("/api/icons/courses").json()["courses"]["bob"]
        assert client.get(f"/api/icons/course/{tail}").status_code == 200


# -- the Trainer ships no Nintendo pictures ------------------------------------------------------------------------

# sha256 of the 11 course portraits the Trainer shipped until 2026-11-09 (ui/assets/course_icons) that were the
# game's own paintings. Fingerprints, not pictures: they let this test recognise one coming back. Rainbow Ride's and
# Tick Tock Clock's were Griffin's own screenshots and still ship (SHIPPED_PICTURES).
REMOVED_NINTENDO_PICTURES = {
    "392f4d6ebedcbab1830bb292abb01f76af128267ab67e008ff7f7a7c7da605bf": "bbh.webp",
    "5079d99930ce71b8445429804d004f183df8046a98ddf209f2940f040b4a5d28": "bitfs.webp",
    "1d46fcd4f13c1e34ec0870cf49e914f1615cedd1c06bc83464b2f637649af039": "bob.webp",
    "784b8ad4eb51c0a80bce2dbf25421df131830cdba019796b3ae802b260f5bee7": "ccm.webp",
    "024fc0c9825825dcbdde0fcb466132c13ea4017be8454b8a45251ab62b66d519": "jrb.webp",
    "caed74713f8073f603416d8e51675946aa1cbe6d92ae44953bb7ff6765f91cf1": "lll.webp",
    "01f445df09b2d7909f11f6565b463f64296a64cf43a2a93218b9cc91b9e37532": "princess_secret.webp",
    "9ca4faa6535e81261b981c23d9f0dd24f075b7f1a2f626c34f0a3f9ccaf834c4": "thi.webp",
    "af962094e48122614f486a22edabbdd445f9c1b2239a83596e0a0dc69ab1b534": "ttm.webp",
    "c73d0ecf2b8cac6703a96c8d8aa126566c80bc33dd1339e7e0ec238d8ecd5c17": "wdw.webp",
    "4c040c89ea05d031d24bcc7940ea7866918e174088a0729ecf1de2db8592d41f": "wf.webp",
}
# The course pictures the Trainer still ships, by their bytes: Griffin's own in-game screenshots (2026-11-09: "we
# should use the icons we already have. Those are screenshots I personally took from in-game.").
SHIPPED_PICTURES = {
    "rr.webp": "784189cf578e707cee315c064826d8b25726fb468d4c4a41a5234aa319afb5a1",
    "ttc.webp": "1a072c36694500f4722e3bd3924505bb12ce7b4ffd69856e24225a6390776ce2",
}
SHIPPED_TAILS = {stem: f"shipped/{name}" for stem, name in ca.SHIPPED.items()}
_IMAGE = re.compile(r"\.(png|webp|jpe?g|gif|avif|bmp)$", re.I)


def tracked_images() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    return [name for name in listed.decode("utf-8").split("\0") if name and _IMAGE.search(name)]


def test_no_tracked_file_is_a_removed_nintendo_picture():
    found = {}
    for name in tracked_images():
        path = REPO / name
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in REMOVED_NINTENDO_PICTURES:
                found[name] = REMOVED_NINTENDO_PICTURES[digest]
    assert found == {}, (
        "a course portrait came back into the repository; the app cuts them from the player's ROM "
        f"(sm64_events/course_art.py): {found}")


def test_the_frozen_app_bundles_no_course_portraits():
    """The frozen bundle's ui/ tree is exactly the tracked ui files (tools/bundle_policy.py::ui_inputs)."""
    import sys
    sys.path.insert(0, str(REPO / "tools"))
    try:
        from bundle_policy import ui_inputs
    finally:
        sys.path.remove(str(REPO / "tools"))
    shipped = set()
    for path in ui_inputs(REPO):
        relative = path.relative_to(UI).as_posix()
        if relative.startswith("assets/course_icons/"):
            shipped.add(relative.removeprefix("assets/course_icons/"))
            assert SHIPPED_PICTURES.get(path.name) == hashlib.sha256(path.read_bytes()).hexdigest(), relative
        if _IMAGE.search(relative):
            assert hashlib.sha256(path.read_bytes()).hexdigest() not in REMOVED_NINTENDO_PICTURES, relative
    assert shipped == set(SHIPPED_PICTURES) == set(ca.SHIPPED.values()), shipped   # exactly his two screenshots

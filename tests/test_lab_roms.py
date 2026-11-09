"""The Ghost Lab's ROM store against a fake Project64 and fake folders: what it finds, keeps and refuses.

No real ROM is used: every image is 8 MiB of filler under a Usamune header, made here.
"""
from __future__ import annotations

import ast
import hashlib
import json
from array import array
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.scenelab import roms
from sm64_events.scenelab.roms import FOLDER, GAME, PICKED, ROM_BYTES, RomRefused, RomStore
from sm64_events.server.lab_roms_api import create_rom_router

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"us": (b"SM64 USAMUNE v1.93u", b"E"), "jp": (b"SM64 USAMUNE v1.93j", b"J"),
         "vanilla": (b"SUPER MARIO 64", b"E"), "later": (b"SM64 USAMUNE v1.94u", b"E")}


def rom(kind: str = "us", *, fill: int = 0, size: int = ROM_BYTES) -> bytes:
    """A big-endian image: a cartridge header naming `kind`, then filler that `fill` makes distinct."""
    name, country = NAMES[kind]
    data = bytearray(bytes((n + fill) & 0xFF for n in range(256)) * (size // 256))
    header = bytearray(0x40)
    header[0:4] = b"\x80\x37\x12\x40"
    header[0x20:0x34] = name.ljust(20, b" ")
    header[0x3B:0x3F] = b"NSM" + country
    data[:0x40] = header
    return bytes(data)


def word_swapped(data: bytes) -> bytes:
    words = array("I", data)
    words.byteswap()
    return words.tobytes()


def half_swapped(data: bytes) -> bytes:
    halves = array("H", data)
    halves.byteswap()
    return halves.tobytes()


class FakeProject64:
    """What the store asks of `Pj64Memory`. It holds the image word-swapped, as Project64 1.6 does, and has no
    way to write: the store must never need one."""

    def __init__(self, image: bytes | None, pid: int = 4242):
        self.image, self.pid, self.running = image, pid, True
        self.attaches = self.image_reads = 0
        self.torn = False

    def attach(self, pid=None):
        self.attaches += 1
        return self.running

    def alive(self):
        return self.running

    def detach(self):
        pass

    def rom_header(self):
        return word_swapped(self.image[:0x40]) if self.image else None

    def cartridge_image(self, size):
        self.image_reads += 1
        if self.torn or self.image is None:
            return None
        return word_swapped(self.image)[:size]


class FakeRegistry:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, subkey, name):
        return self.values.get(name)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def store_for(tmp_path, *, game=None, pj64=None, registry=None, clock=None) -> RomStore:
    return RomStore(tmp_path / "lab" / "roms", pj64_dir=lambda: pj64,
                    registry=(lambda: registry) if registry is not None else (lambda: FakeRegistry()),
                    reader=lambda: game if game is not None else FakeProject64(None),
                    clock=clock or Clock())


def snapshot(folder: Path) -> dict:
    """Every file under `folder` with its size, mtime and hash: proof nothing there was written."""
    return {str(p.relative_to(folder)): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(folder.rglob("*")) if p.is_file()}


# ---- what a ROM is ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("order", [lambda d: d, word_swapped, half_swapped], ids=["z64", "n64", "v64"])
@pytest.mark.parametrize("region", ["us", "jp"])
def test_each_usamune_build_is_known_by_its_header_in_every_dump_order(order, region):
    image = rom(region)
    assert roms.to_big_endian(order(image)) == image
    assert roms.identify(roms.to_big_endian(order(image))) == region


@pytest.mark.parametrize("image", [rom("vanilla"), rom("later"), rom("us", size=ROM_BYTES // 2),
                                   rom("us")[:-1], b"\x00" * ROM_BYTES], ids=["vanilla", "v1.94", "4 MiB", "short", "zeros"])
def test_anything_else_is_not_a_usamune_rom(image):
    assert roms.identify(image) is None


def test_a_us_name_with_the_japanese_country_letter_is_neither():
    image = bytearray(rom("us"))
    image[0x3E] = ord("J")
    assert roms.identify(bytes(image)) is None


@pytest.mark.parametrize("region, label", [("us", "Super Mario 64 Usamune v1.93u"), ("jp", "Super Mario 64 Usamune v1.93j")])
def test_the_missing_sentence_names_the_exact_rom_in_one_sentence(tmp_path, region, label):
    sentence = store_for(tmp_path).missing_reason(region)
    assert label in sentence and sentence.endswith(".") and sentence.count(". ") == 0
    assert "Project64" in sentence and "choose" in sentence


# ---- A: the running game ---------------------------------------------------------------------------------------


def test_the_running_game_is_copied_in_the_n64s_order_and_found_from_then_on(tmp_path):
    game = FakeProject64(rom("us"))
    store = store_for(tmp_path, game=game)
    assert store.rom_for("us") is None
    assert store.copy_from_game() == "us"
    copy = store.rom_for("us")
    assert copy is not None and copy.read_bytes() == rom("us")
    assert store.missing_reason("us") is None and store.missing_reason("jp") is not None
    record = json.loads(copy.with_suffix(".json").read_text(encoding="utf-8"))
    assert record["source"] == GAME and record["sha256"] == hashlib.sha256(rom("us")).hexdigest()
    assert RomStore(store.root, pj64_dir=lambda: None, registry=None).rom_for("us") == copy, "it survives a restart"


def test_one_cartridge_in_one_process_is_read_once(tmp_path):
    game = FakeProject64(rom("us"))
    store = store_for(tmp_path, game=game)
    for _ in range(5):
        store.copy_from_game()
    assert game.image_reads == 1 and game.attaches == 1


def test_a_reader_someone_else_attached_is_used_and_kept_attached(tmp_path):
    """The setup observer keeps one reader per Project64 process; the store borrows it and opens none."""
    borrowed = FakeProject64(rom("jp"))
    borrowed.detach = lambda: (_ for _ in ()).throw(AssertionError("the store must not detach a borrowed reader"))
    own = FakeProject64(None)
    store = store_for(tmp_path, game=own)
    assert store.copy_from(borrowed) == "jp" and store.copy_from(borrowed) is None
    assert borrowed.image_reads == 1 and own.attaches == 0
    assert store.rom_for("jp").read_bytes() == rom("jp")


def test_an_image_lost_mid_read_is_tried_again_and_never_kept(tmp_path):
    game = FakeProject64(rom("us"))
    game.torn = True
    store = store_for(tmp_path, game=game)
    assert store.copy_from_game() is None and store.rom_for("us") is None
    game.torn = False
    assert store.copy_from_game() == "us"


def test_another_game_running_keeps_nothing(tmp_path):
    store = store_for(tmp_path, game=FakeProject64(rom("vanilla")))
    assert store.copy_from_game() is None
    assert not store.root.exists() or not any(store.root.iterdir())


def test_no_project64_is_looked_for_at_most_every_two_seconds(tmp_path):
    game, clock = FakeProject64(None), Clock()
    game.running = False
    store = store_for(tmp_path, game=game, clock=clock)
    for _ in range(4):
        store.copy_from_game()
    assert game.attaches == 1
    clock.now += roms.ATTACH_RETRY_S
    store.copy_from_game()
    assert game.attaches == 2


def test_the_game_wins_over_a_file_with_other_bytes(tmp_path):
    store = store_for(tmp_path, game=FakeProject64(rom("us", fill=7)))
    store.adopt(rom("us", fill=1))
    assert store.copy_from_game() == "us"
    assert store.rom_for("us").read_bytes() == rom("us", fill=7)
    store.adopt(rom("us", fill=2))
    assert store.rom_for("us").read_bytes() == rom("us", fill=7), "a later pick never replaces the game's copy"


# ---- B: Project64's folders ------------------------------------------------------------------------------------


@pytest.fixture
def project64(tmp_path):
    """A Project64 folder as players keep one: other games, a misnamed dump, a folder, a big file."""
    folder = tmp_path / "Project64 1.6"
    folder.joinpath("roms").mkdir(parents=True)
    (folder / "Project64.exe").write_bytes(b"MZ")
    (folder / "roms" / "Super Mario 64 (U) [!].z64").write_bytes(rom("vanilla"))
    (folder / "roms" / "SM64_USAMUNE_v1.93j.n64").write_bytes(rom("jp"))          # z64 order, .n64 name
    (folder / "roms" / "Usamune patcher").mkdir()
    (folder / "roms" / "big.z64").write_bytes(rom("us") + b"\x00" * 4096)
    return folder


def test_the_folder_scan_finds_each_region_by_header_and_writes_nothing_there(tmp_path, project64):
    elsewhere = tmp_path / "my games" / "practice.bin"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(word_swapped(rom("us")))
    registry = FakeRegistry({"RecentFile1": str(elsewhere), "Rom Directory": str(project64 / "roms")})
    before = snapshot(tmp_path / "my games"), snapshot(project64)
    store = store_for(tmp_path, pj64=project64, registry=registry)
    assert store.rom_for("us").read_bytes() == rom("us")
    assert store.rom_for("jp").read_bytes() == rom("jp")
    records = {region: json.loads((store.root / f"{region}.json").read_text(encoding="utf-8")) for region in ("us", "jp")}
    assert records["us"]["source"] == records["jp"]["source"] == FOLDER
    assert records["us"]["from"] == str(elsewhere)
    assert (snapshot(tmp_path / "my games"), snapshot(project64)) == before


def test_a_folder_the_caller_names_is_scanned_when_setup_has_no_record(tmp_path, project64):
    (project64 / "roms" / "practice.v64").write_bytes(half_swapped(rom("us")))
    store = store_for(tmp_path, pj64=None)
    assert sorted(store.scan_folders(project64)) == ["jp", "us"]
    assert store.rom_for("us").read_bytes() == rom("us") and store.rom_for("jp").read_bytes() == rom("jp")


def test_a_scan_reads_no_file_of_a_version_already_kept_past_its_header(tmp_path, project64, monkeypatch):
    """Most players have no JP ROM, so the scan for it repeats; their US file must not be read whole each time."""
    (project64 / "roms" / "SM64_USAMUNE_v1.93j.n64").unlink()
    (project64 / "roms" / "Usamune US.z64").write_bytes(rom("us"))
    store = store_for(tmp_path, pj64=project64)
    store.adopt(rom("us"))
    sizes = []
    real_open = Path.open

    class Counted:
        def __init__(self, path, file):
            self.path, self.file = path, file

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.file.close()

        def read(self, size=-1):
            sizes.append((self.path.name, size))
            return self.file.read(size)

        def seek(self, *args):
            return self.file.seek(*args)

    def counting_open(self, *args, **kwargs):
        return Counted(self, real_open(self, *args, **kwargs))

    monkeypatch.setattr(Path, "open", counting_open)
    assert store.scan_folders() == []
    assert ("Usamune US.z64", 0x40) in sizes and ("Usamune US.z64", ROM_BYTES + 1) not in sizes


def test_a_scan_that_found_nothing_waits_before_the_next(tmp_path, project64):
    clock = Clock()
    store = store_for(tmp_path, pj64=project64, clock=clock)
    assert store.rom_for("us") is None
    (project64 / "roms" / "Usamune US.z64").write_bytes(rom("us"))
    assert store.rom_for("us") is None, "within the wait, no second scan"
    clock.now += roms.SCAN_RETRY_S
    assert store.rom_for("us") is not None


def test_a_damaged_copy_is_never_handed_out(tmp_path, project64):
    store = store_for(tmp_path, game=FakeProject64(rom("us")))
    store.copy_from_game()
    copy = store.rom_for("us")
    damaged = bytearray(copy.read_bytes())
    damaged[0x1000] ^= 0xFF
    copy.write_bytes(bytes(damaged))
    assert store.rom_for("us") is None
    assert store.missing_reason("us") is not None


# ---- C: a file the player chooses ------------------------------------------------------------------------------


def test_a_chosen_file_is_kept_whatever_its_name_or_order(tmp_path):
    store = store_for(tmp_path)
    assert store.adopt(half_swapped(rom("jp")), region="jp") == "jp"
    assert store.rom_for("jp").read_bytes() == rom("jp")
    assert json.loads((store.root / "jp.json").read_text(encoding="utf-8"))["source"] == PICKED


def test_a_chosen_file_of_the_other_region_is_kept_and_named(tmp_path):
    store = store_for(tmp_path)
    assert store.adopt(rom("jp"), region="us") == "jp"
    assert store.rom_for("us") is None and store.rom_for("jp") is not None


@pytest.mark.parametrize("data", [rom("vanilla"), rom("later"), b"not a rom", rom("us")[:ROM_BYTES // 2]],
                         ids=["vanilla", "v1.94", "text", "half"])
def test_a_chosen_file_that_is_neither_is_refused_with_one_sentence(tmp_path, data):
    store = store_for(tmp_path)
    with pytest.raises(RomRefused) as refused:
        store.adopt(data, region="us")
    assert "Super Mario 64 Usamune v1.93u" in str(refused.value)
    assert not store.root.exists() or not any(store.root.iterdir())


# ---- the route ------------------------------------------------------------------------------------------------


def client_for(store) -> TestClient:
    app = FastAPI()
    app.include_router(create_rom_router(store))
    return TestClient(app)


def test_the_route_copies_the_game_on_ask_and_keeps_a_pick(tmp_path):
    game = FakeProject64(rom("us"))
    client = client_for(store_for(tmp_path, game=game))
    regions = client.get("/api/scene-lab/roms").json()["regions"]
    assert regions["us"]["found"] and regions["us"]["source"] == GAME and regions["us"]["reason"] is None
    assert not regions["jp"]["found"] and "Super Mario 64 Usamune v1.93j" in regions["jp"]["reason"]
    answer = client.post("/api/scene-lab/roms/pick?region=jp", content=rom("jp")).json()
    assert answer["stored"] == "jp" and answer["regions"]["jp"]["found"]


def test_the_route_refuses_a_wrong_file_and_a_huge_one_in_plain_words(tmp_path):
    client = client_for(store_for(tmp_path))
    wrong = client.post("/api/scene-lab/roms/pick?region=us", content=rom("vanilla"))
    assert wrong.status_code == 400 and "Super Mario 64 Usamune v1.93u" in wrong.json()["detail"]
    huge = client.post("/api/scene-lab/roms/pick", content=b"\x00" * (3 * ROM_BYTES))
    assert huge.status_code == 413 and "too large" in huge.json()["detail"]
    assert client.post("/api/scene-lab/roms/pick?region=eu", content=rom("us")).status_code == 400


# ---- the contracts the plan names ------------------------------------------------------------------------------


def test_the_frozen_interface_answers_from_the_default_store(tmp_path, monkeypatch):
    monkeypatch.setattr(roms, "_default", store_for(tmp_path, game=FakeProject64(rom("us"))))
    assert roms.rom_for("us") is None
    assert "v1.93u" in roms.missing_reason("us")
    roms.default_store().copy_from_game()
    assert roms.rom_for("us") is not None and roms.missing_reason("us") is None
    with pytest.raises(ValueError):
        roms.rom_for("eu")


def test_the_default_store_lives_under_the_lab_root(monkeypatch):
    monkeypatch.setattr(roms, "_default", None)
    from sm64_events.core import paths
    assert roms.default_store().root == paths.scene_lab_root() / "roms"
    monkeypatch.setattr(roms, "_default", None)


@pytest.mark.parametrize("module", ["src/sm64_events/scenelab/roms.py", "src/sm64_events/server/lab_roms_api.py"])
def test_the_rom_never_leaves_this_computer(module):
    """No network door in the ROM's path: a ROM copy may never be uploaded (plan D1, section 2)."""
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imported & {"socket", "urllib", "http", "requests", "httpx", "aiohttp", "ssl", "ftplib", "smtplib"}
    assert "community" not in (ROOT / module).read_text(encoding="utf-8")

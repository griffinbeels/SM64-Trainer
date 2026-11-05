"""The setup check: the user's Project64 setup, compared with the tested setup.

Every Project64 here is a temp folder and a fake registry; nothing reads the
real machine. The tested setup's hashes are swapped for the fake files' own,
so "match" means the same comparison the real baseline makes.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.core import renderer_settings
from sm64_events.core.capturelayer import LayerStatus, REGISTRY_DLL_SUBKEY, REGISTRY_KEY
from sm64_events.core.diagnostic_privacy import safe_snapshot
from sm64_events.core.diagnostics import SETUP_CHECK_TITLE, setup_check_markdown
from sm64_events.core.setup_profile import (
    FLAGGED, NO_ROM, SetupProfile, load_baseline, parse_rdb, setup_profile_for,
)
from sm64_events.server.setup_api import create_setup_router

US = {"state": "supported", "region": "us", "name": "SM64 USAMUNE v1.93u", "rdb_key": "AFD13A9D-8622568E-C:45"}
USAMUNE_ENTRY = "[AFD13A9D-8622568E-C:45]\nInternal Name=SM64 USAMUNE v1.93u\nRDRAM Size=8\nCounter Factor=1\nCPU Type=Interpreter\n"
TESTED_FILES = {"emulator_build": ("Project64.exe", b"wermi v7"), "rsp_plugin": ("Plugin/RSP.dll", b"fixups rsp"),
                "audio_plugin": ("Plugin/AziAudio.dll", b"azimer"),
                "controller_plugin": ("Plugin/pj64raphnetraw_1player.dll", b"raphnet")}


class FakeRegistry:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, subkey, name):
        return self.values.get((subkey, name))


def fake_baseline():
    baseline = load_baseline()
    for item in baseline["items"]:
        if item["id"] in TESTED_FILES:
            item["sha256"] = hashlib.sha256(TESTED_FILES[item["id"]][1]).hexdigest()
    return baseline


def fake_registry(overrides=None):
    values = {(REGISTRY_DLL_SUBKEY, "Graphics Dll"): "sm64_trainer_gfx.dll",
              (REGISTRY_DLL_SUBKEY, "Audio Dll"): "AziAudio.dll",
              (REGISTRY_DLL_SUBKEY, "Controller Dll"): "pj64raphnetraw_1player.dll",
              (REGISTRY_KEY, "Default RDRAM Size"): 8388608, (REGISTRY_KEY, "CPU Type"): 1}
    values.update(overrides or {})
    return FakeRegistry({key: value for key, value in values.items() if value is not None})


def fake_folder(root: Path) -> Path:
    pj64 = root / "games" / "Project64 1.6"
    (pj64 / "Plugin").mkdir(parents=True)
    for name, data in TESTED_FILES.values():
        (pj64 / name).write_bytes(data)
    (pj64 / "Plugin" / "GLideN64.ini").write_bytes(renderer_settings.disable(None))
    (pj64 / "Project64.rdb").write_text("// header\n[Meta]\nAuthor=x\n//[EOF]\n" + USAMUNE_ENTRY, encoding="latin-1")
    return pj64


def profile(registry, tmp_path, **kwargs):
    kwargs.setdefault("program_dirs", (str(tmp_path / "Program Files"),))
    return SetupProfile(registry, local_appdata=str(tmp_path / "appdata"), baseline=fake_baseline(),
                        version_reader=kwargs.pop("version_reader", lambda path: None), known_builds={}, **kwargs)


def layer(pj64):
    return {"pj64_dir": str(pj64), "plugin_dir": str(pj64 / "Plugin")}


def by_id(check):
    return {item["id"]: item for item in check["items"]}


def test_the_tested_setup_matches_every_item(tmp_path):
    pj64 = fake_folder(tmp_path)
    check = profile(fake_registry(), tmp_path).compare(layer(pj64), US)
    assert {item["id"]: item["status"] for item in check["items"]} == {row: "match" for row in by_id(check)}
    assert len(check["items"]) == 10 and check["flagged"] == [] and check["unknown"] == 0
    assert check["rom"] == US["rdb_key"] and check["rdb"] == "Project64 folder"
    assert all(item["fix"] is None for item in check["items"])


def stock_check(tmp_path):
    """The AMD tester's shape: stock 1.6, Jabo plugins, no Usamune rdb entry in
    the folder, its settings shadowed into VirtualStore by Windows. The UI
    tests draw this same comparison."""
    pj64 = tmp_path / "Program Files" / "Project64 1.6"
    (pj64 / "Plugin").mkdir(parents=True)
    (pj64 / "Project64.exe").write_bytes(b"stock 1.6")
    for name in ("Jabo_Direct3D8.dll", "Jabo_Dsound.dll", "Jabo_DInput.dll", "RSP.dll"):
        (pj64 / "Plugin" / name).write_bytes(name.encode())
    (pj64 / "Project64.rdb").write_text(USAMUNE_ENTRY, encoding="latin-1")
    shadow = tmp_path / "appdata" / "VirtualStore" / pj64.relative_to(pj64.anchor)
    shadow.mkdir(parents=True)
    # The copy Project64 really reads: Windows hands it the shadow first.
    (shadow / "Project64.rdb").write_text("[Meta]\nAuthor=x\n//[EOF]\n", encoding="latin-1")
    registry = fake_registry({(REGISTRY_DLL_SUBKEY, "Graphics Dll"): "Jabo_Direct3D8.dll",
                                  (REGISTRY_DLL_SUBKEY, "Audio Dll"): "Jabo_Dsound.dll",
                                  (REGISTRY_DLL_SUBKEY, "Controller Dll"): "Jabo_DInput.dll",
                                  (REGISTRY_KEY, "Default RDRAM Size"): 4194304})
    return profile(registry, tmp_path, version_reader=lambda path: "1.6.0.0").compare(layer(pj64), US)


def test_stock_project64_under_program_files_with_jabo_and_no_rom_entry(tmp_path):
    check = stock_check(tmp_path)
    items = by_id(check)
    stock = hashlib.sha256(b"stock 1.6").hexdigest()
    assert check["rdb"] == "VirtualStore"
    assert items["graphics_plugin"]["status"] == "differs"
    assert items["graphics_plugin"]["yours"] == "Jabo_Direct3D8.dll"
    assert items["frame_buffer_emulation"]["status"] == "differs"
    assert items["memory_size"]["yours"] == "4 MB (Project64's default)" and items["memory_size"]["status"] == "differs"
    # Neither default is readable evidence, so neither is guessed.
    assert items["counter_factor"]["status"] == "unknown"
    assert (items["cpu_core"]["status"], items["cpu_core"]["yours"]) == ("unknown", "Project64's default (setting 1)")
    assert items["emulator_build"]["yours"] == f"Project64 1.6.0.0 (sha256 {stock[:12]})"
    assert items["emulator_build"]["yours_sha256"] == stock
    assert items["install_location"]["yours"] == "Program Files (settings kept in VirtualStore)"
    assert items["audio_plugin"]["status"] == items["rsp_plugin"]["status"] == "differs"
    assert check["flagged"] == ["graphics_plugin", "frame_buffer_emulation", "memory_size",
                                "emulator_build", "rsp_plugin", "audio_plugin", "install_location"]
    assert check["required_differs"] == 3
    for item in check["items"]:
        if item["status"] != "match":
            assert item["fix"] and item["link"].startswith("https://")


def test_a_default_value_resolves_from_the_registry_or_stays_unknown(tmp_path):
    pj64 = fake_folder(tmp_path)
    (pj64 / "Project64.rdb").write_text(USAMUNE_ENTRY.replace("RDRAM Size=8", "RDRAM Size=Default")
                                        .replace("Counter Factor=1", "Counter Factor=Default"), encoding="latin-1")
    items = by_id(profile(fake_registry(), tmp_path).compare(layer(pj64), US))
    assert (items["memory_size"]["status"], items["memory_size"]["yours"]) == ("match", "8 MB (Project64's default)")
    assert items["counter_factor"]["status"] == "unknown"
    unread = by_id(profile(fake_registry({(REGISTRY_KEY, "Default RDRAM Size"): None}), tmp_path)
                   .compare(layer(pj64), US))
    assert unread["memory_size"]["status"] == "unknown"


def test_another_controller_plugin_is_noted_and_never_a_problem(tmp_path):
    pj64 = fake_folder(tmp_path)
    (pj64 / "Plugin" / "NRage_DInput8_V2.dll").write_bytes(b"nrage")
    check = profile(fake_registry({(REGISTRY_DLL_SUBKEY, "Controller Dll"): "NRage_DInput8_V2.dll"}),
                    tmp_path).compare(layer(pj64), US)
    controller = check["items"][-1]
    assert controller["id"] == "controller_plugin" and controller["importance"] == "noted"
    assert controller["status"] == "differs" and controller["yours"].startswith("NRage_DInput8_V2.dll (sha256 ")
    assert "That is fine" in controller["fix"]
    assert check["flagged"] == [] and "controller_plugin" not in check["flagged"]


def test_without_a_usamune_rom_the_per_rom_settings_are_unknown(tmp_path):
    pj64 = fake_folder(tmp_path)
    items = by_id(profile(fake_registry(), tmp_path).compare(layer(pj64), {"state": "missing", "rdb_key": None}))
    assert {items[key]["yours"] for key in ("memory_size", "counter_factor", "cpu_core")} == {NO_ROM}
    assert {items[key]["status"] for key in ("memory_size", "counter_factor", "cpu_core")} == {"unknown"}


def test_frame_buffer_emulation_on_in_an_existing_virtualstore_copy_differs(tmp_path):
    pj64 = fake_folder(tmp_path)
    shadow = tmp_path / "appdata" / "VirtualStore" / (pj64 / "Plugin").relative_to(pj64.anchor)
    shadow.mkdir(parents=True)
    (shadow / "GLideN64.ini").write_bytes(renderer_settings.disable(None).replace(b"enable=0", b"enable=1"))
    assert by_id(profile(fake_registry(), tmp_path).compare(layer(pj64), US))["frame_buffer_emulation"]["status"] == "differs"


def test_collection_writes_nothing_and_rehashes_only_a_changed_file(tmp_path):
    pj64 = fake_folder(tmp_path)
    before = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")}
    check = profile(fake_registry(), tmp_path)
    check.compare(layer(pj64), US)
    assert {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*")} == before
    (pj64 / "Plugin" / "RSP.dll").write_bytes(b"another rsp")
    assert by_id(check.compare(layer(pj64), US))["rsp_plugin"]["status"] == "differs"


def test_the_rdb_reader_keeps_the_first_section_and_reads_past_eof():
    sections = parse_rdb("[A]\nX=1 // note\n[A]\nX=2\n//[EOF]\n[B]\nY=3\n")
    assert sections == {"A": {"X": "1"}, "B": {"Y": "3"}}


def test_the_importance_table_names_exactly_the_known_breakers():
    baseline = load_baseline()
    importance = {item["id"]: item["importance"] for item in baseline["items"]}
    assert {key for key, value in importance.items() if value == "required"} == {
        "graphics_plugin", "frame_buffer_emulation", "memory_size", "counter_factor"}
    assert importance["controller_plugin"] == "noted" and "noted" not in FLAGGED
    assert baseline["recorded"] == "2026-10-30" and baseline["source"]
    for item in baseline["items"]:
        assert item["fix"].endswith(".") and item["unknown"].endswith(".") and item["link"].startswith("https://")
        assert "practise" not in item["fix"]


def test_the_report_and_the_automatic_schema_carry_the_check_without_folders(tmp_path):
    pj64 = tmp_path / "Program Files" / "PrivateName" / "Project64 1.6"
    (pj64 / "Plugin").mkdir(parents=True)
    check = profile(fake_registry({(REGISTRY_DLL_SUBKEY, "Graphics Dll"): "Jabo_Direct3D8.dll"}),
                    tmp_path).compare(layer(pj64), US)
    markdown = setup_check_markdown(check)
    assert "| graphics_plugin | required | differs | Jabo_Direct3D8.dll |" in markdown
    assert "PrivateName" not in markdown and str(tmp_path) not in markdown
    automatic = safe_snapshot({"setup": {"check": check}})["setup"]["check"]
    rows = {row["id"]: row for row in automatic["items"]}
    assert rows["graphics_plugin"] == {"id": "graphics_plugin", "status": "differs", "importance": "required",
                                       "shared": True, "yours": "Jabo_Direct3D8.dll",
                                       "tested": "SM64 Trainer (sm64_trainer_gfx.dll)"}
    assert automatic["flagged"][0] == "graphics_plugin" and automatic["rom"] == US["rdb_key"]
    assert "PrivateName" not in json.dumps(automatic)
    assert safe_snapshot({"setup": {"check": {"items": [{"id": "C:\\Users\\x", "status": "differs",
                                                         "importance": "required"}]}}})["setup"]["check"]["items"] == []
    assert SETUP_CHECK_TITLE == "Setup compared with the tested setup"


def _status(pj64):
    return LayerStatus(pj64_dir=str(pj64), pj64_running=True, registry_graphics_dll=None,
                       wrapper_present=False, wrapper_current=False, wrapper_selected=False, wrapped_name=None,
                       layer_alive=False, consented_at=None, plugin_dir=str(pj64 / "Plugin"))


@dataclass
class Layer:
    pj64: Path
    registry: FakeRegistry = field(default_factory=fake_registry)
    local_appdata: str | None = None

    def status(self):
        return _status(self.pj64)


def test_get_setup_and_the_check_route_carry_the_comparison(tmp_path):
    pj64 = fake_folder(tmp_path)
    capture = Layer(pj64)
    made = setup_profile_for(capture)
    made.baseline = fake_baseline()
    made._rows = {item["id"]: item for item in made.baseline["items"]}
    app = FastAPI()
    observed = {"target": {"state": "ready", "pid": 1}, "rom": US, "checks": {}}
    app.include_router(create_setup_router(capture, tmp_path / "mode.json", lambda status: observed, profile=made))
    client = TestClient(app)
    full = client.get("/api/setup").json()["emu"]["setup_check"]
    assert by_id(full)["memory_size"]["status"] == "match"
    alone = client.get("/api/setup/check").json()
    assert alone["items"] == full["items"] and alone["flagged"] == []
    bare = FastAPI()
    bare.include_router(create_setup_router(capture, tmp_path / "mode.json"))
    assert TestClient(bare).get("/api/setup/check").status_code == 404


def test_a_report_reads_the_cartridge_the_poller_saw_not_a_stale_setup_look(tmp_path):
    """The setup screen may have looked before the ROM was open; the poller
    reads the header every second while playing. Neither attaches here."""
    from types import SimpleNamespace as NS
    from sm64_events.server.diagnostics_runtime import setup_check_now
    pj64 = fake_folder(tmp_path)
    made = profile(fake_registry(), tmp_path)
    capture = NS(diagnostic_status=lambda: layer(pj64))
    observer = NS(diagnostic_status=lambda: {"rom": {"state": "missing", "rdb_key": None}})
    stale = by_id(setup_check_now(capture, observer, made))
    assert stale["memory_size"]["yours"] == NO_ROM
    live = by_id(setup_check_now(capture, observer, made, NS(rom_identity=US)))
    assert live["memory_size"]["status"] == "match"
    assert setup_check_now(capture, observer, None) is None

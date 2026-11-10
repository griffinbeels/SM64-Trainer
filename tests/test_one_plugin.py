"""THE GRAPHICS PLUGIN is one file that loads no other plugin and reads no ini of its own.

Until 2026-11-07 Project64 selected a capture wrapper that LoadLibrary'd the
renderer named by `wrapped=` in sm64_trainer_gfx.ini, a second DLL installed
under a non-DLL name. Defender flagged that renderer (Trojan:Win32/Bearfoos.A!ml,
2026-11-06), and a proxy forwarding to a disguised sibling is the shape malware
takes. His ruling (2026-11-07): "the approach that's easiest for the user and
has the least likelihood of being accidentally flagged as malware". So
tools/build_renderer.py links the capture layer into the renderer, and these
pin the result:

- the capture layer's source names no ini and loads no module;
- the staging renames exactly the renderer functions the plugin binds
  (plugin/gfxwrap/renderer_entries.c), so only the capture layer exports the
  Zilmar names Project64 calls;
- a bundled plugin, where one is built, exports the frozen interface and
  carries none of the old wrapper's ini or renderer names.
"""
from __future__ import annotations

import importlib.util
import re
import struct
import zipfile
from pathlib import Path

import pytest

from renderer_fixture import renderer_archive as renderer_archive  # noqa: F401  (fixture)
from sm64_events.core.capturelayer import _build_identity
from sm64_events.core.paths import bundled_plugin_dll

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin" / "gfxwrap"

# The Zilmar 1.3 surface Project64 1.6 looks up, the capture layer's own two
# exports, and the renderer exports the scene lab's hosts and the capture
# layer resolve by name.
ZILMAR = {"CaptureScreen", "ChangeWindow", "CloseDLL", "DllAbout", "DllConfig", "DllTest", "DrawScreen",
          "FBGetFrameBufferInfo", "FBRead", "FBWList", "FBWrite", "GetDllInfo", "InitiateGFX", "MoveScreen",
          "ProcessDList", "ProcessRDPList", "ReadScreen", "RomClosed", "RomOpen", "ShowCFB", "UpdateScreen",
          "ViStatusChanged", "ViWidthChanged"}
CAPTURE_LAYER = {"SM64TrainerWrapperIdentity", "SM64RendererOnlyV1"}
RENDERER = {"SM64ReplaySourceV2", "SM64ReplaySourceV3", "SM64ReplaySourceV4", "SM64ReplayContextV1",
            "SM64ReplayContextEvidenceV1", "SM64GhostSourceV3", "SM64ViewerGhostsV1",
            "SM64RendererSettingsFolderV1"}
# What only the old two-file shape needed. None may be in the plugin's bytes.
OLD_SHAPE = (b"sm64_trainer_gfx.ini", "sm64_trainer_gfx.ini".encode("utf-16-le"), b"wrapped=", b"stream=",
             b".renderer", b"GLideN64_SM64Trainer")


def _tool(name):
    spec = importlib.util.spec_from_file_location(f"one_plugin_{name}", ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pe_exports(data: bytes) -> tuple[str, set[str]]:
    """The export directory's DLL name and exported names of a PE32 image."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe:pe + 4] == b"PE\0\0" and struct.unpack_from("<H", data, pe + 24)[0] == 0x10B, "not PE32"
    sections, optional = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    export_rva = struct.unpack_from("<I", data, pe + 24 + 96)[0]
    table = pe + 24 + optional
    spans = [struct.unpack_from("<II4xI", data, table + 40 * n + 8) for n in range(sections)]

    def offset(rva):
        for size, virtual, raw in ((s[0], s[1], s[2]) for s in spans):
            if virtual <= rva < virtual + size:
                return raw + rva - virtual
        raise ValueError(f"rva {rva:#x} outside every section")

    def text(rva):
        at = offset(rva)
        return data[at:data.index(b"\0", at)].decode("ascii")

    directory = offset(export_rva)
    name_rva, _, _, count, _, names_rva = struct.unpack_from("<IIIIII", data, directory + 12)
    names = {text(struct.unpack_from("<I", data, offset(names_rva) + 4 * n)[0]) for n in range(count)}
    return text(name_rva), names


def test_the_capture_layer_loads_no_plugin_and_reads_no_ini():
    for name in ("gfxwrap.c", "renderer_entries.c", "renderer_entries.h"):
        text = (SOURCE / name).read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)   # comments may tell the history
        for forbidden in ("LoadLibrary", "GetPrivateProfile", "fopen", ".ini", "wrapped="):
            assert forbidden not in code, f"{name} uses {forbidden}"


def test_the_plugin_binds_exactly_the_renderer_functions_the_staging_makes(tmp_path, renderer_archive):  # noqa: F811
    """renderer_entries.c names GLideN64_<name> for each renderer function; the
    staging (tools/build_renderer.py stage_renderer_entries) must produce each of
    them from upstream's pinned sources and leave no Zilmar function exported."""
    build = _tool("build_renderer")
    wanted = [*build.RENDERER_ENTRY_FILES, build.RENDERER_DLLMAIN[0]]
    with zipfile.ZipFile(renderer_archive) as bundle:
        for member in bundle.namelist():
            relative = member.split("/", 1)[1] if "/" in member else ""
            if relative in wanted:
                (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / relative).write_bytes(bundle.read(member))
    build.stage_renderer_entries(tmp_path)
    staged = "".join((tmp_path / relative).read_text(encoding="utf-8") for relative in wanted)
    assert "EXPORT void CALL" not in staged and "EXPORT BOOL CALL" not in staged
    made = set(re.findall(r"CALL (GLideN64_\w+)", staged)) | set(re.findall(r"WINAPI (GLideN64_DllMain)\(", staged))
    bound = set(re.findall(r"\b(GLideN64_\w+)\(", (SOURCE / "renderer_entries.c").read_text(encoding="utf-8")))
    assert bound <= made, f"the plugin binds renderer functions the staging never makes: {bound - made}"
    assert made - bound == {"GLideN64_DllAbout"}, "every renderer function but its About box is bound"
    assert {name.removeprefix("GLideN64_") for name in made} == ZILMAR | {"DllMain"}


def test_the_bundled_plugin_exports_the_interface_and_none_of_the_old_shape():
    path = bundled_plugin_dll()
    if path is None:
        pytest.skip("the plugin is not built in this checkout (tools/build_natives.py)")
    data = path.read_bytes()
    library, names = pe_exports(data)
    assert library == "sm64_trainer_gfx.dll"
    wanted = ZILMAR | CAPTURE_LAYER | RENDERER
    assert wanted <= names, f"missing exports: {sorted(wanted - names)}"
    assert [needle for needle in OLD_SHAPE if needle in data] == []
    identity = _build_identity(path)
    assert identity is not None and identity.endswith(b"-gpu-runtime"), "one build id, the capture layer's role"

"""Every native we build says who made it and what it is, and the same sources give the same bytes.

tools/native_identity.py: on 2026-11-06 Defender quarantined our anonymous,
never-seen-before renderer as Trojan:Win32/Bearfoos.A!ml. Each binary now
carries a version resource, and two clean builds in different folders are the
same file. Windows' own version reader (version.dll) and imagehlp's checksum
are the oracles here, not this repository's parser.
"""
import ctypes
import hashlib
import importlib.util
import os
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import native_identity  # noqa: E402

WINDOWS = sys.platform == "win32"


def _tool(name):
    spec = importlib.util.spec_from_file_location(f"identity_test_{name}", ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def version_strings(path: Path) -> dict:
    """The StringFileInfo values Windows itself reads from a file (Explorer's Details tab)."""
    from ctypes import wintypes
    version = ctypes.WinDLL("version")
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return {}
    buffer = ctypes.create_string_buffer(size)
    assert version.GetFileVersionInfoW(str(path), 0, size, buffer)
    found = {}
    for key in ("CompanyName", "ProductName", "FileDescription", "FileVersion", "OriginalFilename",
                "LegalCopyright", "ProductVersion"):
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if version.VerQueryValueW(buffer, f"\\StringFileInfo\\040904B0\\{key}", ctypes.byref(pointer),
                                  ctypes.byref(length)) and length.value:
            found[key] = ctypes.wstring_at(pointer, length.value - 1)
    return found


def link_time(path: Path) -> int:
    data = path.read_bytes()[:4096]
    return struct.unpack_from("<I", data, struct.unpack_from("<I", data, 0x3C)[0] + 8)[0]


# -- what each file says it is --------------------------------------------------

@pytest.mark.parametrize("name", native_identity.SHIPPED)
def test_each_shipped_native_names_its_maker_and_what_it_is(name):
    identity = native_identity.identity_for(name)
    text = native_identity.rc_text(name, native_identity.calendar_version(1_794_000_000))
    for value in ('"CompanyName", "SM64 Trainer"', '"ProductName", "SM64 Trainer"',
                  f'"OriginalFilename", "{name}"', f'"FileDescription", "{identity.description}"'):
        assert value in text
    assert identity.description.startswith("SM64 Trainer") and "Copyright (C)" in identity.copyright


def test_the_plugin_says_it_is_gliden64_under_its_license():
    """One file since 2026-11-07: the plugin IS LINK's GLideN64 with the trainer's additions."""
    identity = native_identity.identity_for("sm64_trainer_gfx.dll")
    assert "GLideN64" in identity.description and "LINK's v4.2" in identity.description
    assert "GNU GPL v2" in identity.description
    assert "Sergey Lipskiy" in identity.copyright and "GNU GPL v2" in identity.copyright
    assert native_identity.SHIPPED == ("sm64_trainer_gfx.dll", "SM64GpuEncoderV1.dll")


def test_an_unnamed_helper_is_still_named_as_ours():
    identity = native_identity.identity_for("probe_host.exe")
    assert identity.description == "SM64 Trainer developer test program (probe_host.exe)"


def test_version_text_refuses_what_a_resource_string_cannot_hold():
    with pytest.raises(ValueError):
        native_identity.rc_text("x.dll", identity=native_identity.Identity('say "hi"', "c"))


def test_the_file_version_is_the_source_date():
    # 2026-11-06 21:27:17 UTC
    assert native_identity.calendar_version(1_794_000_437) == ((2026, 11, 6, 2127), "2026.11.6.2127")


# -- the source date --------------------------------------------------------------

def test_a_source_date_is_the_newest_commit_among_the_inputs():
    files = [ROOT / "tools" / "build_plugin.py", ROOT / "plugin" / "gfxwrap" / "gfxwrap.c"]
    each = [native_identity.source_date([path]) for path in files]
    assert all(date > native_identity.NO_SOURCE_DATE for date in each)
    assert native_identity.source_date(files) == max(each)


def test_a_file_outside_the_checkout_has_no_date(tmp_path):
    (tmp_path / "x.c").write_text("int x;")
    assert native_identity.source_date([tmp_path / "x.c"]) == native_identity.NO_SOURCE_DATE


# -- the PE header ------------------------------------------------------------------

def _minimal_pe(checksum: int = 0) -> bytearray:
    head = bytearray(0x200)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHI", head, 0x84, 0x14C, 0, 1_600_000_000)
    struct.pack_into("<I", head, 0x80 + 24 + 64, checksum)
    return head


def test_stamping_writes_the_date_and_leaves_an_unset_checksum_unset(tmp_path):
    path = tmp_path / "a.dll"
    path.write_bytes(_minimal_pe())
    native_identity.stamp(path, 1_794_000_437)
    assert link_time(path) == 1_794_000_437
    assert struct.unpack_from("<I", path.read_bytes(), 0x80 + 24 + 64)[0] == 0


@pytest.mark.skipif(not WINDOWS, reason="imagehlp is Windows' own checksum")
def test_a_set_checksum_is_recomputed_as_imagehlp_computes_it(tmp_path):
    from ctypes import wintypes
    system_dll = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "version.dll"
    path = tmp_path / "version-copy.dll"
    path.write_bytes(system_dll.read_bytes())
    native_identity.stamp(path, 1_794_000_437)
    imagehlp = ctypes.WinDLL("imagehlp")
    header, computed = wintypes.DWORD(), wintypes.DWORD()
    assert imagehlp.MapFileAndCheckSumW(str(path), ctypes.byref(header), ctypes.byref(computed)) == 0
    assert header.value == computed.value != 0
    assert link_time(path) == 1_794_000_437


# -- the shared compile path --------------------------------------------------------

def test_every_compile_is_deterministic_and_maps_its_folders_away(tmp_path):
    args = native_identity.prepare(None, ["/nologo", "/c", "x.c"], tmp_path / "work", tmp_path / "scratch")
    assert "/experimental:deterministic" in args
    assert f"/pathmap:{native_identity.REPO}={native_identity.REPO_NAME}" in args
    assert f"/pathmap:{tmp_path / 'work'}=\\build" in args
    assert "/link" not in args, "a compile-only call gains no link step"


toolchain = pytest.mark.skipif(not WINDOWS or _tool("build_plugin").find_vcvars32() is None,
                               reason="needs Windows and the MSVC toolchain")


@toolchain
def test_a_helper_built_through_the_shared_path_carries_its_version_resource(tmp_path):
    build = _tool("build_plugin")
    source = tmp_path / "probe.c"
    source.write_text("int main(void) { return 0; }\n")
    target = tmp_path / "viewer_host.exe"
    build._cl(build.find_vcvars32(), ["/nologo", str(source), f"/Fe:{target}", f"/Fo{tmp_path}\\"], tmp_path)
    found = version_strings(target)
    assert found["CompanyName"] == found["ProductName"] == "SM64 Trainer"
    assert found["FileDescription"] == native_identity.identity_for("viewer_host.exe").description
    assert found["OriginalFilename"] == "viewer_host.exe"
    assert found["FileVersion"].startswith("0.0.0.0")
    assert not list(tmp_path.glob("*.version.*")), "the resource is built beside, not in, the output folder"


# -- repeatable builds -----------------------------------------------------------------

@toolchain
def test_a_test_plugin_and_the_encoder_helper_built_twice_are_the_same_bytes(tmp_path, monkeypatch):
    """Two clean builds in different folders, without the test compile cache:
    the same sha256; the shipped helper has the source date in its PE header
    and a readable version. A test plugin links the capture layer as the
    shipped plugin's build does; the shipped plugin's own double build needs
    MSBuild and its prebuilt libraries (opt-in, below)."""
    monkeypatch.setenv("SM64_NATIVE_BUILD_CACHE", "0")
    build = _tool("build_plugin")
    digests = []
    for folder in ("first", "second"):
        built = (build.build_test_plugin(tmp_path / folder), build.build_encoder_helper(tmp_path / folder))
        digests.append(tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in built))
    assert digests[0] == digests[1]
    date = build._build_term()[1]
    path = tmp_path / "first" / "SM64GpuEncoderV1.dll"
    assert link_time(path) == date, "the PE header orders installs: it must be the source date"
    found = version_strings(path)
    assert found["CompanyName"] == "SM64 Trainer" and found["OriginalFilename"] == path.name
    assert found["FileVersion"] == native_identity.calendar_version(date).text
    found = version_strings(tmp_path / "first" / "sm64_trainer_gfx.dll")
    assert "test graphics plugin" in found["FileDescription"] and found["FileVersion"].startswith("0.0.0.0")


@pytest.mark.skipif(os.environ.get("SM64_TEST_PLUGIN_REPRO") != "1",
                    reason="opt-in: two full plugin builds need the renderer's prebuilt libraries "
                           "(SM64_TEST_PLUGIN_REPRO=1)")
def test_the_plugin_built_twice_is_the_same_bytes(tmp_path):
    build = _tool("build_renderer")
    digests = [hashlib.sha256(build.build_graphics_plugin(tmp_path / folder).read_bytes()).hexdigest()
               for folder in ("first", "second")]
    assert digests[0] == digests[1]
    path = tmp_path / "first" / "sm64_trainer_gfx.dll"
    assert link_time(path) == build._build_term()[1]
    found = version_strings(path)
    assert "GLideN64" in found["FileDescription"] and found["CompanyName"] == "SM64 Trainer"
    assert found["OriginalFilename"] == path.name


def test_a_build_id_names_the_toolchain_and_the_source_date(monkeypatch):
    """A toolchain update or a newer source date changes the bytes, so it changes the id."""
    build = _tool("build_plugin")
    before = build.sources_digest()
    monkeypatch.setattr(native_identity, "toolchain", lambda vcvars: "msvc 99.0; sdk 99")
    toolchain_changed = build.sources_digest()
    monkeypatch.setattr(native_identity, "source_date", lambda paths: 2_000_000_000)
    assert len({before, toolchain_changed, build.sources_digest()}) == 3

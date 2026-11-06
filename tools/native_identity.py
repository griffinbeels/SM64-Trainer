"""Who made each native file and what it is, and the same bytes from the same sources.

Every Windows binary this repository compiles (the three shipped natives, the
scene lab's helpers and the test hosts) passes through here on its way out of
the toolchain. Two things happen to it:

* **A version resource** (Explorer's Properties > Details): CompanyName and
  ProductName "SM64 Trainer", a FileDescription that says what the file is,
  OriginalFilename, FileVersion and a LegalCopyright that matches where the
  code came from (`IDENTITIES`). Before 2026-11-06 every native was anonymous:
  no version resource and no signature. Defender then quarantined the
  renderer as Trojan:Win32/Bearfoos.A!ml, a cloud machine-learning verdict on
  one build, in three places at once.
* **A repeatable build.** Two clean builds of the same sources in different
  folders give the same sha256. A file built twice was otherwise two
  never-seen files, and Microsoft's own figure is that "Approximately 96% of
  all malware files detected and blocked ... are observed only once on a
  single computer" (Microsoft Security blog, 2017-07-18). Neither switch is in the toolchain's own help, so each
  is kept for an effect measured on 2026-11-06 (MSVC 14.50, two builds each):
  - cl `/experimental:deterministic` with `/pathmap:<folder>=<name>`: with
    /Brepro alone the renderer's .rdata, .data and .reloc came out in a
    different order from each scratch folder (1,367 differing runs; an object
    carries its source path, and C++ names in anonymous namespaces are hashed
    from it). Each build folder and this checkout map to fixed names
    (`path_maps`). /pathmap alone left a probe object path-dependent, as
    OpenJDK's adoption note says: "with the deterministic build mode enabled,
    the -pathmap flag ... is enabled" (mail.openjdk.org build-dev,
    2020-11, JDK-8256240).
  - link `/Brepro`: without it MSBuild's link of the renderer keeps the wall
    clock in its debug directory (4 bytes). A link that cl drives already
    gets this from /experimental:deterministic (the wrapper and the encoder
    helper come out the same with or without it).
  - not kept: cl `/Brepro` changed nothing once /experimental:deterministic
    was on, and lib's archive is an intermediate the renderer only reads
    objects from.
  tests/test_native_identity.py builds the wrapper and the encoder helper
  twice; the renderer's double build is opt-in (SM64_TEST_RENDERER_REPRO=1).

The shipped natives then get one more step. Project64's installed plugin is
ordered by its PE TimeDateStamp (core/plugin_ownership.py: a trainer never
replaces an installed build with an older one, and released trainers still
compare that field). A hash there would order builds at random, so `stamp`
writes the SOURCE DATE instead: the commit time of the newest commit that
touched the native's inputs (`source_date`, the SOURCE_DATE_EPOCH idea of the
Reproducible Builds project). The same date names the FileVersion
(year.month.day.hhmm, UTC), and every build id hashes it with the toolchain
version, so the id still names every input that changes the bytes.
Uncommitted edits keep the last commit's date; their content still changes
the id. Without git the date is 1980-01-01, older than any other
source-dated build.

Test and lab helpers are never installed or ordered: they get the version
resource with version 0.0.0.0 ("development build") and keep `/Brepro`'s
hash timestamp.
"""
from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]
RECIPE = "1"  # bump when what this module writes into a binary changes
COMPANY = PRODUCT = "SM64 Trainer"
NO_SOURCE_DATE = 315532800  # 1980-01-01 UTC: no git, so no date to name
REPRO_COMPILE = ("/experimental:deterministic",)  # cl, always with path_maps
REPRO_LINK = ("/Brepro",)  # link: a hash instead of the clock
REPO_NAME = r"\sm64-trainer"  # what this checkout's path reads as inside a binary
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def path_maps(*folders: tuple[Path, str]) -> list[str]:
    """cl's /pathmap for this checkout and the given (folder, fixed name) pairs."""
    pairs = [(REPO, REPO_NAME), *folders]
    return [f"/pathmap:{Path(folder)}={name}" for folder, name in pairs]


OURS_GPL3 = "Copyright (C) SM64 Trainer contributors. GNU GPL v3 or later."


class Identity(NamedTuple):
    description: str
    copyright: str


# What each output is. The renderer and the wrapper carry GLideN64's GPL v2,
# as the wrapper's own About box and renderer/README.md say.
IDENTITIES = {
    "sm64_trainer_gfx.dll": Identity(
        "SM64 Trainer graphics plugin for Project64: loads the trainer's renderer and records practice replays",
        "Copyright (C) SM64 Trainer contributors. GNU GPL v2, like the GLideN64 renderer it loads."),
    "GLideN64_SM64Trainer.dll": Identity(
        "SM64 Trainer renderer: GLideN64 (LINK's v4.2 build, Luna-Project64) with the trainer's replay "
        "overlay, under the GNU GPL v2",
        "GLideN64: Copyright (C) Sergey Lipskiy and the GLideN64 contributors. Overlay: Copyright (C) "
        "SM64 Trainer contributors. GNU GPL v2."),
    "SM64GpuEncoderV1.dll": Identity(
        "SM64 Trainer hardware video encoder for practice replays (loaded by the trainer's own helper process)",
        "Copyright (C) SM64 Trainer contributors. GNU GPL v3 or later. Includes NVIDIA's nvEncodeAPI.h (MIT)."),
    "viewer_host.exe": Identity("SM64 Trainer scene viewer helper (developer tool): draws saved scenes", OURS_GPL3),
    "replay_host.exe": Identity("SM64 Trainer scene lab replay host (developer tool)", OURS_GPL3),
    "scene_cpu_hooks.dll": Identity("SM64 Trainer scene lab CPU hooks (developer tool)", OURS_GPL3),
    "closure_probe.exe": Identity("SM64 Trainer scene lab closure probe (developer tool)", OURS_GPL3),
    "ghost_sound_host.exe": Identity("SM64 Trainer scene lab ghost sound helper (developer tool)", OURS_GPL3),
    "tap_offline_host.exe": Identity("SM64 Trainer scene tap test host (developer tool)", OURS_GPL3),
    "gfxwrap_host.exe": Identity("SM64 Trainer graphics plugin test host (developer tool)", OURS_GPL3),
    "fake_gfx.dll": Identity("SM64 Trainer stand-in renderer for tests (developer tool)", OURS_GPL3),
    "eos-transport.exe": Identity("SM64 Trainer online transport helper (EOS)",
                                  "Copyright (C) SM64 Trainer contributors. MIT License."),
    "eos-core-test.exe": Identity("SM64 Trainer online transport tests (developer tool)",
                                  "Copyright (C) SM64 Trainer contributors. MIT License."),
}
SHIPPED = ("sm64_trainer_gfx.dll", "SM64GpuEncoderV1.dll", "GLideN64_SM64Trainer.dll")


def identity_for(filename: str) -> Identity:
    """A named output's identity; any other output is a developer build of this repository."""
    return IDENTITIES.get(filename) or Identity(
        f"SM64 Trainer developer test program ({filename})", OURS_GPL3)


class Version(NamedTuple):
    numbers: tuple[int, int, int, int]
    text: str


DEVELOPMENT = Version((0, 0, 0, 0), "0.0.0.0 (development build)")


def calendar_version(epoch: int) -> Version:
    """year.month.day.hhmm of a UTC time: each part fits the resource's 16 bits."""
    when = datetime.fromtimestamp(epoch, timezone.utc)
    numbers = (when.year, when.month, when.day, when.hour * 100 + when.minute)
    return Version(numbers, ".".join(map(str, numbers)))


def source_date(paths) -> int:
    """The commit time of the newest commit touching any of `paths`."""
    relative = []
    for path in paths:
        try:
            relative.append(Path(path).resolve().relative_to(REPO).as_posix())
        except ValueError:
            continue
    if not relative:
        return NO_SOURCE_DATE
    try:
        found = subprocess.run(["git", "log", "-1", "--format=%ct", "--", *relative], cwd=REPO,
                               capture_output=True, text=True, creationflags=_NO_WINDOW, check=False,
                               stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return NO_SOURCE_DATE
    value = found.stdout.strip()
    return int(value) if found.returncode == 0 and value.isdigit() else NO_SOURCE_DATE


def toolchain(vcvars: Path | None) -> str:
    """The MSVC tools version and the Windows SDK that vcvars selects (the newest
    one with um/winsdkver.h, as its winsdk.bat does): both change the bytes."""
    if vcvars is None:
        return "no-toolchain"
    tools = Path(vcvars).with_name("Microsoft.VCToolsVersion.default.txt")
    try:
        vc = tools.read_text(encoding="utf-8").strip()
    except OSError:
        vc = "unknown"
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "Include"
    versions = []
    if kits.is_dir():
        for folder in kits.iterdir():
            if (folder / "um" / "winsdkver.h").is_file() and re.fullmatch(r"\d+(\.\d+)*", folder.name):
                versions.append(tuple(int(part) for part in folder.name.split(".")))
    sdk = ".".join(map(str, max(versions))) if versions else "unknown"
    return f"msvc {vc}; sdk {sdk}"


def build_term(inputs, vcvars: Path | None) -> tuple[bytes, int]:
    """What a shipped native's build id hashes beyond its sources: this
    recipe, the toolchain and the source date. Returns (term, date)."""
    date = source_date([*inputs, Path(__file__)])
    text = f"identity {RECIPE}\0{toolchain(vcvars)}\0source date {date}\0"
    return text.encode() + Path(__file__).read_bytes() + b"\0", date


# -- the version resource -----------------------------------------------------

def _rc_string(value: str) -> str:
    if not value or any(not (" " <= char <= "~") or char in '"\\' for char in value):
        raise ValueError(f"version resource text must be plain printable ASCII without quotes: {value!r}")
    return f'"{value}"'


def rc_text(filename: str, version: Version = DEVELOPMENT, identity: Identity | None = None) -> str:
    """The .rc source of one file's VERSIONINFO. No #include: the constants
    are spelled as numbers (VOS_NT_WINDOWS32, VFT_APP or VFT_DLL)."""
    identity = identity or identity_for(filename)
    kind = 2 if filename.lower().endswith(".dll") else 1
    numbers = ",".join(map(str, version.numbers))
    strings = {
        "CompanyName": COMPANY,
        "FileDescription": identity.description,
        "FileVersion": version.text,
        "InternalName": Path(filename).stem,
        "LegalCopyright": identity.copyright,
        "OriginalFilename": filename,
        "ProductName": PRODUCT,
        "ProductVersion": version.text,
    }
    values = "\n".join(f"            VALUE {_rc_string(key)}, {_rc_string(value)}" for key, value in strings.items())
    return (f"// Generated by tools/native_identity.py for {filename}.\n"
            "1 VERSIONINFO\n"
            f"FILEVERSION {numbers}\nPRODUCTVERSION {numbers}\n"
            "FILEFLAGSMASK 0x3F\nFILEFLAGS 0x0\nFILEOS 0x40004\n"
            f"FILETYPE {kind:#x}\nFILESUBTYPE 0x0\n"
            "BEGIN\n    BLOCK \"StringFileInfo\"\n    BEGIN\n        BLOCK \"040904B0\"\n        BEGIN\n"
            f"{values}\n"
            "        END\n    END\n    BLOCK \"VarFileInfo\"\n    BEGIN\n"
            "        VALUE \"Translation\", 0x409, 1200\n    END\nEND\n")


def compile_resource(vcvars: Path, rc_source: Path, res: Path) -> Path:
    """rc.exe from the toolchain vcvars sets up, hidden; the .res it writes."""
    import native_build_cache  # one captured vcvars environment per process
    import shutil
    env = native_build_cache.environment(vcvars)
    compiler = shutil.which("rc", path=env.get("PATH") or env.get("Path"))
    if compiler is None:
        raise RuntimeError(f"rc.exe not on the PATH {vcvars} sets")
    result = native_build_cache._spawn([compiler, "/nologo", "/fo", str(res), str(rc_source)], rc_source.parent, env)
    if result.returncode != 0 or not res.is_file():
        raise RuntimeError(f"rc failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    return res


def resource(vcvars: Path, filename: str, folder: Path, version: Version = DEVELOPMENT,
             identity: Identity | None = None) -> Path:
    """<stem>.version.res for `filename` in `folder` (the name stays the same
    for every build, so the test compile cache keys it by content)."""
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / f"{Path(filename).stem}.version.rc"
    source.write_text(rc_text(filename, version, identity), encoding="ascii", newline="\r\n")
    return compile_resource(vcvars, source, folder / f"{Path(filename).stem}.version.res")


# -- the compile path every helper shares (tools/build_plugin.py::_cl) -------

_OUTPUT = re.compile(r"^/Fe:?(?P<path>.+\.(?:exe|dll))$", re.IGNORECASE)


def output_of(args: list[str]) -> str | None:
    """The .exe or .dll a cl call links, from its /Fe option."""
    for arg in args:
        match = _OUTPUT.match(arg)
        if match:
            return Path(match["path"]).name
    return None


def prepare(vcvars: Path, args: list[str], cwd: Path, scratch: Path, *, version: Version = DEVELOPMENT,
            identity: Identity | None = None) -> list[str]:
    """One cl call's arguments made repeatable, and for a call that links an
    .exe or .dll, given that file's version resource (compiled into `scratch`).
    The working folder reads as \\build inside the objects."""
    args = [str(arg) for arg in args]
    link = args.index("/link") if "/link" in args else len(args)
    compiler, linker = args[:link], args[link + 1:]
    compiler += [flag for flag in REPRO_COMPILE if flag not in compiler]
    if not any(arg.startswith("/pathmap:") for arg in compiler):
        compiler += path_maps((Path(cwd), r"\build"))
    if "/c" in compiler:
        return compiler + (["/link", *linker] if link < len(args) else [])
    output = output_of(compiler)
    if output is not None:
        compiler.append(str(resource(vcvars, output, scratch, version, identity)))
    linker += [flag for flag in REPRO_LINK if flag not in linker]
    return [*compiler, "/link", *linker]


# -- the source date in the PE header -----------------------------------------

def _pe_offset(data: bytes | bytearray) -> int:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError("not a PE image")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset + 24 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        raise ValueError("not a PE image")
    return offset


def pe_checksum(data: bytes | bytearray, checksum_at: int) -> int:
    """The PE optional header's CheckSum (imagehlp's algorithm): the 16-bit
    words with end-around carry, the field itself left out, plus the length."""
    padded = bytes(data[:checksum_at]) + b"\0\0\0\0" + bytes(data[checksum_at + 4:])
    padded += b"\0" * (len(padded) % 2)
    total = sum(memoryview(padded).cast("H"))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total + len(data)


def stamp(path: Path, epoch: int) -> None:
    """Write `epoch` into the COFF header's TimeDateStamp, and refresh the
    checksum when the linker set one. Runs before any signature exists: an
    Authenticode signature covers this field."""
    data = bytearray(path.read_bytes())
    offset = _pe_offset(data)
    struct.pack_into("<I", data, offset + 8, epoch & 0xFFFFFFFF)
    checksum_at = offset + 24 + 64  # same place in PE32 and PE32+
    if struct.unpack_from("<I", data, checksum_at)[0]:
        struct.pack_into("<I", data, checksum_at, pe_checksum(data, checksum_at))
    partial = path.with_name(path.name + ".stamping")
    partial.write_bytes(data)
    os.replace(partial, path)


def scratch_folder(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

"""Build ffmpeg.exe, the one the app bundles, from pinned sources with a pinned portable toolchain.

    uv run python tools/build_ffmpeg.py                 # the build for these inputs (cached), its path
    uv run python tools/build_ffmpeg.py --out DIR       # also copy it into DIR
    uv run python tools/build_ffmpeg.py --rebuild       # build again even if cached (repeatability check)
    uv run python tools/build_ffmpeg.py --notices       # rewrite licenses/ffmpeg/BUILD-README.txt

SPDX-License-Identifier: GPL-3.0-or-later

Every input is pinned by URL and SHA-256 in ffmpeg/build-inputs.json: the
sources (the ones PyAV's own FFmpeg comes from, pyav-ffmpeg 8.1.1-2), a
portable MSYS2 base and the exact UCRT64 packages of the toolchain. The
toolchain is unpacked under the build cache
(%LOCALAPPDATA%/SM64Trainer/build-cache/ffmpeg/msys64): nothing is installed
on the machine, and no PATH or registry setting changes. Every compiler runs
hidden at below-normal priority. ffmpeg/README.md says why this build and not
a downloaded one (the GPL asks that we can publish its complete source), what
it contains, and what the app asks of it.

Two builds of the same inputs are the same bytes: no paths of this machine
enter the binary (the configure line names none; libraries are built with
-ffile-prefix-map), the PE header carries no link time, and SOURCE_DATE_EPOCH
is fixed. The result is cached by a digest of the inputs, the patches and the
build script, and checked against `expected_sha256` when that is pinned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INPUTS = REPO / "ffmpeg" / "build-inputs.json"
PATCHES = REPO / "ffmpeg" / "patches"
sys.path.insert(0, str(REPO / "tools"))
import source_patch  # noqa: E402
BELOW_NORMAL = 0x00004000
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SOURCE_DATE_EPOCH = 1_788_000_000   # fixed; the binary carries no build date anyway
COMPONENT_KINDS = ("encoder", "decoder", "demuxer", "muxer", "filter", "bsf", "parser", "protocol", "indev")
# ffmpeg's own listing flag for each kind (verify()).
LISTING = {"encoder": "-encoders", "decoder": "-decoders", "demuxer": "-demuxers", "muxer": "-muxers",
           "filter": "-filters", "bsf": "-bsfs", "parser": None, "protocol": "-protocols", "indev": "-devices"}
# Components whose listed name is not their configure name (libavformat/allformats.c).
LISTED_AS = {("demuxer", "mpegps"): "mpeg", ("demuxer", "image_png_pipe"): "png_pipe",
             ("demuxer", "pcm_s16le"): "s16le"}


def inputs() -> dict:
    return json.loads(INPUTS.read_text(encoding="utf-8"))


def default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SM64Trainer" / "build-cache" / "ffmpeg"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_id() -> str:
    """A digest of what makes the binary: the inputs (the expected result
    left out), the patches and the build script itself, for a stand-in work
    folder (the real one never reaches the binary). Editing this file's
    checks or text leaves the cached build valid."""
    spec = inputs()
    spec.pop("expected_sha256", None)
    recipe = build_script(spec, Path("/work"), 1)
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode() + b"\0" + recipe.encode())
    for patch in sorted(PATCHES.glob("*.json")):
        digest.update(patch.name.encode() + b"\0" + patch.read_bytes() + b"\0")
    return digest.hexdigest()[:32]


def _rmtree(path: Path) -> None:
    """Remove a tree, read-only files included (MSYS2 and source archives carry some)."""
    def writable_then_retry(function, target, _error):
        os.chmod(target, 0o700)
        function(target)
    if path.exists():
        shutil.rmtree(path, onexc=writable_then_retry)


def fetch(folder: Path, url: str, sha256: str) -> Path:
    """A pinned download, verified; a cached file with other bytes is replaced."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / url.rsplit("/", 1)[-1]
    if target.is_file() and _sha256(target) == sha256:
        return target
    request = urllib.request.Request(url, headers={"User-Agent": "SM64Trainer build_ffmpeg"})
    partial = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(request, timeout=900) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)
    if _sha256(partial) != sha256:
        partial.unlink()
        raise ValueError(f"{url} is not the pinned file ({sha256})")
    partial.replace(target)
    return target


def _run_bash(msys: Path, script: str, log: Path, cwd: Path) -> None:
    """One bash script in the portable UCRT64 shell, hidden, at below-normal
    priority (everything it starts inherits that), output to `log`."""
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"PATH", "MSYSTEM", "CHERE_INVOKING", "MSYS2_PATH_TYPE", "CFLAGS", "CXXFLAGS",
                                "LDFLAGS", "PKG_CONFIG_PATH", "CPATH", "LIBRARY_PATH", "CL"}}
    env.update({"MSYSTEM": "UCRT64", "CHERE_INVOKING": "1", "MSYS2_PATH_TYPE": "minimal",
                "PATH": str(msys / "usr" / "bin") + r";C:\Windows\System32;C:\Windows"})
    script_path = log.with_suffix(".sh")
    script_path.write_bytes(script.encode("utf-8"))
    with log.open("wb") as out:
        result = subprocess.run([str(msys / "usr" / "bin" / "bash.exe"), "-lc", f'. "$(cygpath -u "{script_path}")"'],
                                cwd=str(cwd), env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, check=False, creationflags=_NO_WINDOW | BELOW_NORMAL)
    if result.returncode != 0:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
        raise RuntimeError(f"{script_path.name} failed ({result.returncode}); log at {log}\n{tail}")


def toolchain(root: Path, spec: dict) -> Path:
    """The portable MSYS2 with exactly the pinned packages, under `root/msys64`."""
    msys2 = spec["msys2"]
    marker_value = hashlib.sha256(json.dumps(msys2, sort_keys=True).encode()).hexdigest()
    msys = root / "msys64"
    marker = msys / ".sm64-toolchain"
    if marker.is_file() and marker.read_text(encoding="ascii").strip() == marker_value:
        return msys
    downloads = root / "downloads"
    base = fetch(downloads, msys2["base"]["url"], msys2["base"]["sha256"])
    packages = [fetch(downloads, item["url"], item["sha256"]) for item in msys2["packages"]]
    _rmtree(msys)
    with tarfile.open(base, "r:xz") as archive:
        archive.extractall(root, filter="tar")
    # The first login initialises the shell and pacman's keyring; then the
    # pinned files, and nothing else, are installed from disk.
    names = " ".join(f'"$(cygpath -u "{path}")"' for path in packages)
    _run_bash(msys, f"set -e\npacman -U --noconfirm --needed {names}\n", root / "toolchain.log", root)
    marker.write_text(marker_value + "\n", encoding="ascii")
    return msys


def unpack_sources(spec: dict, root: Path, work: Path) -> None:
    """Each pinned source under `work/src/<name>`, then the tracked patches."""
    for name, item in spec["sources"].items():
        archive = fetch(root / "sources", item["url"], item["sha256"])
        with tarfile.open(archive) as bundle:
            bundle.extractall(work / "src", filter="data")
        (work / "src" / item["folder"]).rename(work / "src" / name)
    for patch in sorted(PATCHES.glob("*.json")):
        source_patch.apply_patch(work / "src", patch)


def components(spec: dict) -> dict[str, list[str]]:
    """Every component the binary has, by kind: what the app runs, then what
    the tests make their fixtures with (`test_components`)."""
    merged = {kind: list(spec["components"].get(kind, [])) for kind in COMPONENT_KINDS}
    for kind, names in spec.get("test_components", {}).items():
        merged[kind] += [name for name in names if name not in merged[kind]]
    return merged


def configure_args(spec: dict) -> list[str]:
    """FFmpeg's configure line: the pinned flags, then each component list.
    It names no path of this machine (ffmpeg -version prints it)."""
    args = [f"--extra-version={spec['version_suffix']}", *spec["configure"]]
    for kind, names in components(spec).items():
        if names:
            args.append(f"--enable-{kind}={','.join(names)}")
    return args


def _quote(arg: str) -> str:
    return "'" + arg.replace("'", "'\\''") + "'"


def build_script(spec: dict, work: Path, jobs: int) -> str:
    ffmpeg_configure = " ".join(_quote(arg) for arg in configure_args(spec))
    return f"""set -euo pipefail
W="$(cygpath -u '{work}')"
P="$W/prefix"
PW="$(cygpath -m "$P")"
J={jobs}
export SOURCE_DATE_EPOCH={SOURCE_DATE_EPOCH}
export PKG_CONFIG_PATH="$P/lib/pkgconfig"
export CPATH="$PW/include"
export LIBRARY_PATH="$PW/lib"
MAP="-ffile-prefix-map=$(cygpath -m "$W")=/build"
export CFLAGS="-O2 $MAP"
export CXXFLAGS="-O2 $MAP"
mkdir -p "$P/include" "$P/lib/pkgconfig" "$P/bin"

echo "== zlib"
cd "$W/src/zlib"
make -f win32/Makefile.gcc -j$J LOC="$MAP" libz.a
make -f win32/Makefile.gcc install SHARED_MODE=0 BINARY_PATH="$P/bin" INCLUDE_PATH="$P/include" LIBRARY_PATH="$P/lib"

echo "== nv-codec-headers"
make -C "$W/src/nv-codec-headers" PREFIX="$P" install

echo "== amf-headers"
cp -r "$W/src/amf-headers/AMF" "$P/include/"

echo "== x264"
cd "$W/src/x264"
./configure --prefix="$P" --enable-static --disable-cli --disable-lsmash --disable-swscale --disable-ffms \\
    --disable-opencl --enable-strip --extra-cflags="$MAP"
make -j$J
make install

echo "== dav1d"
cd "$W/src/dav1d"
meson setup build --prefix="$PW" --buildtype=release --default-library=static \\
    -Denable_tools=false -Denable_tests=false
ninja -C build -j $J install

echo "== libvpl"
cd "$W/src/libvpl"
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PW" -DBUILD_SHARED_LIBS=OFF \\
    -DINSTALL_LIB=ON -DINSTALL_DEV=ON -DINSTALL_EXAMPLES=OFF -DBUILD_EXPERIMENTAL=OFF -DBUILD_TESTS=OFF \\
    -DBUILD_EXAMPLES=OFF -DBUILD_TOOLS=OFF
cmake --build build -j $J
cmake --install build
# A static libvpl is C++: its pkg-config file names no C++ runtime, which only a shared build hides.
sed -i 's/^Libs: \\(.*\\)$/Libs: \\1 -lstdc++/' "$P/lib/pkgconfig/vpl.pc"

echo "== ffmpeg"
cd "$W/src/ffmpeg"
./configure {ffmpeg_configure}
make -j$J ffmpeg.exe
"""


def build(root: Path, jobs: int) -> Path:
    """Build ffmpeg.exe for the pinned inputs into `root/out/<build id>/`."""
    spec = inputs()
    msys = toolchain(root, spec)
    work = root / "work"
    _rmtree(work)
    (work / "src").mkdir(parents=True)
    unpack_sources(spec, root, work)
    _run_bash(msys, build_script(spec, work, jobs), root / "build.log", work)
    built = work / "src" / "ffmpeg" / "ffmpeg.exe"
    if not built.is_file():
        raise RuntimeError(f"the build reported success but {built} is missing")
    target = root / "out" / build_id() / "ffmpeg.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built, target)
    _rmtree(work)
    return target


def ensure(root: Path | None = None, jobs: int = 12, rebuild: bool = False, *, build_missing: bool = True) -> Path:
    """The ffmpeg.exe for these inputs: cached by build id, built when missing
    (unless `build_missing` is off: a read-only check), and refused when it is
    not the pinned result."""
    root = root or default_root()
    cached = root / "out" / build_id() / "ffmpeg.exe"
    if not (rebuild or cached.is_file() or build_missing):
        raise RuntimeError(f"ffmpeg.exe for these inputs is not built yet ({cached}): "
                           "run uv run python tools/build_ffmpeg.py")
    path = build(root, jobs) if rebuild or not cached.is_file() else cached
    expected = inputs().get("expected_sha256")
    if expected and _sha256(path) != expected:
        raise RuntimeError(f"{path} ({_sha256(path)}) is not the pinned build ({expected}): "
                           "same inputs, other bytes")
    return path


NOTICES = REPO / "licenses" / "ffmpeg"
SOURCE_ARCHIVE = "SM64Trainer-third-party-source-ffmpeg.tar"
# What is inside ffmpeg.exe, by the folder of licenses/ffmpeg that holds its texts.
NOTICE_FOLDERS = (
    ("FFmpeg", "FFmpeg 8.1.1 (libavcodec, libavformat, libavfilter, libavutil, libswscale, libswresample "
               "and the ffmpeg program)", "GPL-3.0-or-later as built; LICENSE.md explains"),
    ("x264", "x264 at b35605ac", "GPL-2.0-or-later"),
    ("dav1d", "dav1d 1.5.3", "BSD-2-Clause"),
    ("libvpl", "Intel libvpl 2.16.0 (the dispatcher for Intel's encoders)", "MIT"),
    ("nv-codec-headers", "NVIDIA's nv-codec-headers n13.0.19.0", "MIT (the header's own notice)"),
    ("AMF", "AMD's AMF headers 1.5.0", "MIT (the headers' own notice)"),
    ("zlib", "zlib 1.3.1", "Zlib"),
    ("mingw-w64-runtime", "the MinGW-w64 C runtime start-up code", "the notices in its text"),
    ("winpthreads", "MinGW-w64 winpthreads", "MIT and BSD notices"),
    ("gcc-runtime", "libgcc and libstdc++ from GCC 16.2.0 (libvpl is C++)",
     "GPL-3.0 with the GCC Runtime Library Exception (COPYING.RUNTIME; the GPL v3 is FFmpeg/COPYING.GPLv3)"),
)


def version_name(spec: dict) -> str:
    """What `ffmpeg -version` prints for this build: the release, then the suffix."""
    release = spec["sources"]["ffmpeg"]["folder"].removeprefix("ffmpeg-")
    return f"{release}-{spec['version_suffix']}"


def notices_text(spec: dict | None = None) -> str:
    """licenses/ffmpeg/BUILD-README.txt for these inputs. The tracked file
    must equal it (tests/test_build_ffmpeg.py), so a change to the recipe
    cannot ship with a stale notice."""
    spec = spec or inputs()
    attached = json.loads((REPO / "licenses" / "third-party-sources.json").read_text(encoding="utf-8"))
    runtime = [item for item in attached["archives"][SOURCE_ARCHIVE]["sources"]
               if "ffmpeg.exe" in item["used_in"] and item["url"].startswith("https://repo.msys2.org/")]
    lines = [
        "ffmpeg.exe for SM64 Trainer",
        "",
        f"Version: {version_name(spec)}",
        "",
        f"SHA-256: {spec.get('expected_sha256', '(not pinned yet)')}",
        "",
        "License: GPL version 3 or later. FFmpeg is configured with --enable-gpl",
        "--enable-version3, and links x264 (GPL v2 or later).",
        "",
        "Source: the source archives on the release page of the SM64 Trainer",
        f"version this copy came with: {SOURCE_ARCHIVE}, with its .sha256.",
        "It holds every archive below. The recipe is tools/build_ffmpeg.py and",
        "ffmpeg/build-inputs.json in the trainer's repository at that release's tag:",
        f"the sources PyAV's own FFmpeg comes from (pyav-ffmpeg {spec['pyav_ffmpeg']['tag']}), built",
        f"with a portable MSYS2 {spec['msys2']['environment']} toolchain whose every package is pinned.",
        "",
        "Sources:",
    ]
    for name, item in spec["sources"].items():
        lines += [f"  {name}: {item['url']}", f"    sha256 {item['sha256']}"]
    lines += ["", "The runtime that is linked into the program (MSYS2 source packages):"]
    for item in runtime:
        lines += [f"  {item['url']}", f"    sha256 {item['sha256']}"]
    lines += ["", "Inside ffmpeg.exe, with the license texts in this folder:"]
    for folder, what, license in NOTICE_FOLDERS:
        lines += [f"  {folder}/: {what}", f"    {license}"]
    lines += ["", "Configuration (what ffmpeg -version prints):"]
    lines += [f"  {arg}" for arg in configure_args(spec)]
    return "\n".join(lines) + "\n"


def _listed(ffmpeg: Path, flag: str) -> str:
    return subprocess.run([str(ffmpeg), "-hide_banner", flag], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True, timeout=60, creationflags=_NO_WINDOW).stdout


def listed_names(text: str) -> set[str]:
    """The names in one of ffmpeg's listings: flagged rows (` D   mov,mp4,... QuickTime`,
    ` V....D libx264 ...`) and bare rows (`-bsfs`, `-protocols`)."""
    names = set()
    for flagged in re.findall(r"(?m)^\s*[A-Z.|]+\s+([\w,]+)\s", text):
        names.update(flagged.split(","))
    names.update(re.findall(r"(?m)^\s*(\w+)\s*$", text))
    return names


def verify(ffmpeg: Path) -> list[str]:
    """Every pinned component the binary does not list (an empty list passes),
    and a configuration that names this machine. FFmpeg's configure ignores a
    component name it does not know, so this is the check that a list entry
    took effect."""
    wanted = components(inputs())
    missing = []
    for kind, flag in LISTING.items():
        if flag is None:
            continue
        names = listed_names(_listed(ffmpeg, flag))
        missing += [f"{kind} {name}" for name in wanted[kind]
                    if LISTED_AS.get((kind, name), name) not in names]
    version = _listed(ffmpeg, "-version")
    if re.search(r"(?i)c:[/\\]users|/c/users", version):
        missing.append("configuration names a local path")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=None, help="build cache folder")
    parser.add_argument("--out", type=Path, default=None, help="also copy ffmpeg.exe into this folder")
    parser.add_argument("--jobs", type=int, default=12, help="parallel compilers (default 12)")
    parser.add_argument("--rebuild", action="store_true", help="build again even when cached")
    parser.add_argument("--notices", action="store_true",
                        help="write licenses/ffmpeg/BUILD-README.txt for these inputs, and nothing else")
    args = parser.parse_args()
    if args.notices:
        (NOTICES / "BUILD-README.txt").write_bytes(notices_text().encode("utf-8"))
        print(NOTICES / "BUILD-README.txt")
        return 0
    try:
        path = ensure(args.root, args.jobs, args.rebuild)
        missing = verify(path)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as failure:
        print(failure)
        return 2
    if missing:
        print("built, but missing: " + ", ".join(missing))
        return 2
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, args.out / "ffmpeg.exe")
    print(f"{path}\nsha256 {_sha256(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

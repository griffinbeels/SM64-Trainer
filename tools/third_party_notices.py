# tools/third_party_notices.py
"""The license texts the packaged app carries, staged into `_internal/licenses/`.

The app's own LICENSE plus three sources, one folder:

- the tracked `licenses/` folder: everything vendored by hand (ffmpeg.exe,
  PyAV's FFmpeg libraries, the renderer and the Qt inside it, the NVIDIA
  header, Preact, htm, WebView2). `licenses/README.md` names each one's
  license and where its source is;
- the interpreter's own LICENSE.txt, which also covers the DLLs Python
  brings (OpenSSL, libffi, bzip2 ...);
- the license files of every Python distribution the app ACTUALLY bundles,
  read from the built exe rather than pyproject: PyInstaller bundles what is
  imported, which includes transitive dependencies pyproject never names.

`stage` refuses a bundled distribution with no license file, and an
ffmpeg.exe or PyAV that is not the build `licenses/` describes, so a
dependency bump cannot ship with missing or stale notices.
`tools/build_exe.py` calls it after PyInstaller; the zip and manifest are
made from the finished folder, so the notices reach installs and updates.
"""
import importlib.metadata as metadata
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LICENSES = REPO / "licenses"
OWN_DISTRIBUTION = "sm64-events"
_LICENSE_NAME = re.compile(r"(?i)^(licen[cs]e|copying|notice|authors|copyright)")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def bundled_import_names(app_dir: Path, exe_name: str = "SM64Trainer") -> set[str]:
    """Top-level import names inside a built onedir app: the modules in the
    exe's embedded PYZ plus the packages, extensions and delvewheel `.libs`
    folders PyInstaller copied into `_internal/`."""
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(app_dir / f"{exe_name}.exe"))
    names: set[str] = set()
    for entry in archive.toc:
        if entry.endswith(".pyz"):
            names |= {module.split(".")[0] for module in archive.open_embedded_archive(entry).toc}
    for child in (app_dir / "_internal").iterdir():
        if child.name.endswith(".dist-info") or child.name == "licenses":
            continue
        if child.is_dir():
            names.add(child.name.removesuffix(".libs"))
        elif child.suffix == ".pyd":
            names.add(child.name.split(".")[0])
    return names


def distributions_for(import_names: set[str]) -> dict[str, metadata.Distribution]:
    """The installed distributions providing these import names, by canonical
    name. Standard-library and project modules map to none and are skipped:
    the interpreter's own license covers the first, LICENSE the second."""
    providers = metadata.packages_distributions()
    found = {}
    for name in import_names:
        for dist_name in providers.get(name, []):
            if _canonical(dist_name) != OWN_DISTRIBUTION:
                found[_canonical(dist_name)] = metadata.distribution(dist_name)
    return found


def license_files(dist: metadata.Distribution) -> list[metadata.PackagePath]:
    """A distribution's license files as its wheel installed them: anything
    under `.dist-info/licenses/` (PEP 639), or a LICENSE/COPYING/NOTICE/
    AUTHORS file at the top of its `.dist-info`."""
    found = []
    for path in dist.files or []:
        parts = path.parts
        if len(parts) < 2 or not parts[0].endswith(".dist-info"):
            continue
        if parts[1] == "licenses" or (len(parts) == 2 and _LICENSE_NAME.match(path.name)):
            found.append(path)
    return found


def ffmpeg_version(ffmpeg: Path) -> str:
    out = subprocess.run([str(ffmpeg), "-hide_banner", "-version"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", check=True, timeout=60,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    match = re.match(r"ffmpeg version (\S+)", out)
    if not match:
        raise RuntimeError(f"cannot read the version of {ffmpeg}: {out[:200]!r}")
    return match.group(1)


def recorded_version(folder: Path, readme: str) -> str:
    """The `Version:` line a notices folder records for the build it describes."""
    match = re.search(r"^Version:\s*(\S+)", (folder / readme).read_text(encoding="utf-8"), re.M)
    if not match:
        raise RuntimeError(f"{folder / readme} names no 'Version:' line")
    return match.group(1)


def check_recorded_builds(ffmpeg: Path | None, licenses: Path = LICENSES) -> None:
    """Refuse an ffmpeg.exe or PyAV that `licenses/` does not describe."""
    if ffmpeg is not None:
        shipped, described = ffmpeg_version(ffmpeg), recorded_version(licenses / "ffmpeg", "BUILD-README.txt")
        if shipped != described:
            raise RuntimeError(
                f"the bundled ffmpeg is {shipped}, but licenses/ffmpeg describes {described}. "
                "Replace licenses/ffmpeg/LICENSE and BUILD-README.txt with the new build's "
                "LICENSE and README.txt, and update its row in licenses/README.md.")
    shipped, described = metadata.version("av"), recorded_version(licenses / "pyav-ffmpeg", "README.md")
    if shipped != described:
        raise RuntimeError(
            f"the bundled PyAV is {shipped}, but licenses/pyav-ffmpeg describes {described}. "
            "Its FFmpeg build (scripts/ffmpeg-latest.json at that PyAV tag) names the "
            "pyav-ffmpeg release whose scripts/pkg.py lists every library; refresh the texts.")


def stage(internal: Path, import_names: set[str], ffmpeg: Path | None,
          licenses: Path = LICENSES) -> Path:
    """Write `<internal>/licenses/`; return it. Raises when a notice is missing."""
    check_recorded_builds(ffmpeg, licenses)
    out = internal / "licenses"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(licenses, out)
    (out / "SM64-Trainer").mkdir()
    shutil.copy2(REPO / "LICENSE", out / "SM64-Trainer" / "LICENSE")

    interpreter = Path(sys.base_prefix) / "LICENSE.txt"
    if not interpreter.is_file():
        raise RuntimeError(f"the interpreter's license is missing: {interpreter}")
    version = ".".join(map(str, sys.version_info[:3]))
    (out / "python" / f"CPython-{version}").mkdir(parents=True)
    shutil.copy2(interpreter, out / "python" / f"CPython-{version}" / "LICENSE.txt")

    missing = []
    for name, dist in sorted(distributions_for(import_names).items()):
        files = license_files(dist)
        if not files:
            if not (licenses / "python" / name).is_dir():    # a tracked text stands in
                missing.append(f"{dist.metadata['Name']} {dist.version}")
            continue
        folder = out / "python" / f"{name}-{dist.version}"
        for path in files:
            target = folder.joinpath(*path.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dist.locate_file(path), target)
    if missing:
        raise RuntimeError(
            "these bundled Python packages install no license file: " + ", ".join(missing)
            + ". Add each one's license text under licenses/python/<name>/ and list it "
            "in licenses/README.md.")
    return out

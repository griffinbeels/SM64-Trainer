"""Build a self-contained private beta installer; never invoke releases or GitHub.

    python tools/build_beta.py --config <public-beta-config.json> [--ffmpeg <exe>]
    python tools/build_beta.py --config <public-beta-config.json> --check

Natives must already match this checkout; this wrapper refuses stale outputs
instead of rebuilding them. The full frozen build is an explicit separate step.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sm64_events.core.build_channel import (BETA_APP_DIR, BuildChannel, CONFIG_FILENAME,
                                           read_metadata)  # noqa: E402
from build_exe import COLLECT, SEP, _source_date_epoch, app_args, needs_reexec  # noqa: E402
from make_manifest import build_zip  # noqa: E402
from beta_ghosts import (ApprovedCollection, COLLECTION_NAME, matches, review_collection,
                         candidate_author, seed_descriptor)  # noqa: E402
from sm64_events.bootstrap.beta_payload import append_payload  # noqa: E402

DIST = REPO / "dist" / "private-beta"
WORK = REPO / "build" / "private-beta"
SEED_DESCRIPTOR = "beta-ghost-seed.json"
_FORBIDDEN_NAMES = {"tracker.db", "tracker.lock", "server.pid", "window.json",
                    "update_state.json", "capture_layer.json", "replay_settings.json",
                    "ui_log.jsonl", "library_adoptions.json", "tracker_mode.json"}
_FORBIDDEN_DIRS = {"replays", "logs", "replay_buffer", "ghost_population",
                   "community_cache", "compare_cache"}
_FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".gho", ".ghost", ".ghc1",
                       ".sm64ghosts", ".mp4", ".mkv"}
# PyInstaller's NumPy collection includes these two immutable vendor fixtures.
# Exempt their exact bytes only from the hidden-name guard; private guards remain.
_VENDOR_HIDDEN = {
    "_internal/numpy/f2py/tests/src/assumed_shape/.f2py_f2cmap":
        "cdfb8e4a19aea2dcdc2c84039d515a011c2f33aea22eb398ce9aae2060db88a5",
    "_internal/numpy/f2py/tests/src/f2cmap/.f2py_f2cmap":
        "7f0b33ca669685c58edbdeaee538475b9c8c02416107a12d1d6aaaa5cf450152",
}
README = """SM64 Trainer Beta - private test build

Extract the complete ZIP into a writable folder. Keep SM64TrainerBeta.exe and
the entire _internal folder together, then double-click SM64TrainerBeta.exe.
This beta uses its own SM64TrainerBeta profile under Windows LocalAppData.
It does not import or migrate your public trainer history. Keep the public
trainer closed while recording: both apps share one recorder and Project64.
An explicit plugin install changes the shared Project64 installation and can
affect the public trainer's next emulator launch.

Use your invited staging account for community testing. Signing in and sharing
are optional; practice works offline. Sheet imports start unchecked.
New accounts share their best ghosts automatically after consent. You can turn
sharing off in Settings; existing shared ghosts can be removed there too.
Public app updates are disabled. Run the next private installer when provided;
your separate beta profile survives. Portable ZIP users can replace the whole
extracted application folder instead.
Never replace the _internal folder while this beta is running.
"""


def check_prerequisites(ffmpeg: str | None) -> str:
    """Read-only prerequisite check. No compiler, installation or data changes."""
    resolved = ffmpeg or shutil.which("ffmpeg")
    if not resolved or not Path(resolved).is_file() or Path(resolved).name.lower() != "ffmpeg.exe":
        raise RuntimeError("FFmpeg is required: supply the existing ffmpeg.exe with --ffmpeg")
    missing = [name for name in ["PyInstaller", *COLLECT, "httpx", "authlib"]
               if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError("build environment is missing: " + ", ".join(missing))
    import build_natives
    build_natives.ensure(build=False, strict=True)
    from build_defaults_seed import main as check_defaults
    if check_defaults(["--check"]):
        raise RuntimeError("defaults seed is stale; regenerate it before packaging")
    args = app_args(resolved)
    for index, arg in enumerate(args[:-1]):
        if arg in ("--add-data", "--add-binary"):
            source = args[index + 1].rsplit(SEP, 1)[0]
            if not Path(source).exists():
                raise RuntimeError(f"required bundled asset is missing: {Path(source).name}")
    return str(Path(resolved).resolve())


def beta_args(ffmpeg: str, metadata_path: Path, *, dist: Path = DIST,
              work: Path = WORK) -> list[str]:
    return app_args(ffmpeg, name=BETA_APP_DIR, metadata_path=metadata_path,
                    distpath=dist, workpath=work / "pyinstaller", specpath=work / "spec")


def installer_args(*, work: Path = WORK) -> list[str]:
    """Small offline stub; the app ZIP is appended after PyInstaller completes."""
    return [str(REPO / "beta_installer_entry.py"), "--name", "SM64TrainerBetaSetup",
            "--onefile", "--windowed", "--clean", "--noconfirm", "--paths", str(REPO / "src"),
            "--icon", str(REPO / "assets" / "ukiki.ico"), "--distpath", str(work / "installer"),
            "--workpath", str(work / "installer-work"), "--specpath", str(work / "installer-spec")]


def validate_artifact(folder: Path, build: BuildChannel, *, ghosts: ApprovedCollection | None = None,
                      ghost_author: dict | None = None) -> None:
    """Refuse incomplete or contaminated outputs before producing a friend ZIP."""
    if build.channel != "beta" and build.installation != "normal":
        raise RuntimeError("private artifact requires a beta build")
    internal = folder / "_internal"
    app_name = "SM64Trainer" if build.installation == "normal" else BETA_APP_DIR
    required = [folder / f"{app_name}.exe", internal / "ffmpeg.exe",
                internal / CONFIG_FILENAME, internal / "sm64_events" / "ui" / "index.html",
                *[internal / "sm64_events" / "data" / "plugin" / name
                  for name in ("sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll")],
                *[internal / name for name in ("ukiki.ico", "behaviours_us.tsv", "behaviours_jp.tsv",
                    "symbols_us.tsv", "symbols_jp.tsv", "rank_standards.seed.json", "defaults.seed.json",
                    "sheet_ladders.seed.json", "sheet_library.seed.json.gz", "library_overrides.json",
                    "video_checks.seed.json.gz")]]
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"beta artifact is missing: {path.relative_to(folder)}")
    if build.installation == "normal" and not any(
            path.is_file() and path.stat().st_size > 0 for path in internal.glob("python[0-9]*.dll")):
        raise RuntimeError("normal artifact is missing the Python runtime DLL")
    if read_metadata(internal / CONFIG_FILENAME) != build:
        raise RuntimeError("packaged beta metadata does not match the requested build")
    if ghosts is not None:
        descriptor = internal / SEED_DESCRIPTOR
        expected = seed_descriptor(ghosts, ghost_author)
        if ghost_author is not None and (not build.community_enabled
                or ghost_author["issuer"] != build.supabase_url.rstrip("/") + "/auth/v1"):
            raise RuntimeError("packaged ghost author does not match the configured Auth issuer")
        if (not descriptor.is_file() or descriptor.stat().st_size > 4096
                or json.loads(descriptor.read_text(encoding="utf-8")) != expected):
            raise RuntimeError("packaged beta ghost seed does not match the reviewed collection")
    for path in folder.rglob("*"):
        relative = path.relative_to(folder)
        lowered = {part.lower() for part in relative.parts}
        name = path.name.lower()
        vendor_hash = _VENDOR_HIDDEN.get(relative.as_posix())
        known_vendor = (vendor_hash is not None and path.is_file() and 0 < path.stat().st_size <= 4096
                        and hashlib.sha256(path.read_bytes()).hexdigest() == vendor_hash)
        reviewed_ghosts = (ghosts is not None and relative.as_posix() == COLLECTION_NAME
                          and matches(path, ghosts))
        if (path.is_symlink() or not path.resolve().is_relative_to(folder.resolve())
                or lowered & _FORBIDDEN_DIRS
                or any(part.startswith(".") for part in relative.parts) and not known_vendor
                or name in _FORBIDDEN_NAMES or path.suffix.lower() in _FORBIDDEN_SUFFIXES and not reviewed_ghosts
                or name.startswith("sm64trainersetup")
                or name == "sm64trainer.exe" and build.installation != "normal"):
            raise RuntimeError("beta artifact contains an unexpected private or public-release file")


def package_beta(folder: Path, build: BuildChannel, destination: Path, *, ghosts: ApprovedCollection | None = None,
                 ghost_author: dict | None = None) -> Path:
    if ghosts is not None:
        if not matches(ghosts.source, ghosts):
            raise RuntimeError("The approved ghost collection changed before packaging.")
        shutil.copyfile(ghosts.source, folder / COLLECTION_NAME)
        (folder / "_internal" / SEED_DESCRIPTOR).write_text(
            json.dumps(seed_descriptor(ghosts, ghost_author), sort_keys=True) + "\n", encoding="utf-8")
    validate_artifact(folder, build, ghosts=ghosts, ghost_author=ghost_author)
    readme = README
    if ghosts is not None:
        readme += (f"\nIncluded: {ghosts.count} legitimate recordings credited to griffman1212.\n"
                   "These recordings are available automatically as local rivals.\n"
                   "They do not change your PBs or upload as your own recordings.\n"
                   "Some historical recordings have unknown ROM or inputs; these remain unknown.\n")
    if not build.community_url:
        readme = ('OFFLINE BETA: community sign-in and downloads are not configured in this ZIP.\n'
                  'Use it to test local practice, imported ghosts and beta profile isolation.\n\n') + readme
    (folder / "README-BETA.txt").write_text(readme, encoding="utf-8")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{BETA_APP_DIR}-{build.build_id}.zip"
    build_zip(folder, archive)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise RuntimeError("beta ZIP failed its integrity check")
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


def package_installer(stub: Path, archive: Path, build: BuildChannel, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    executable = destination / f"{BETA_APP_DIR}-{build.build_id}.exe"
    digest = append_payload(stub, archive, executable)
    executable.with_suffix(".exe.sha256").write_text(f"{digest}  {executable.name}\n", encoding="utf-8")
    return executable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True,
                        help="explicit packaged beta settings (not an environment file)")
    parser.add_argument("--ffmpeg", help="existing ffmpeg.exe; defaults to PATH")
    parser.add_argument("--ghosts", type=Path, help="explicit curated griffman1212 collection to include")
    parser.add_argument("--ghost-author", type=Path, help="reviewed author Auth issuer/subject binding JSON")
    parser.add_argument("--check", action="store_true", help="check prerequisites without building")
    parser.add_argument("--version", help="normal beta version, e.g. 1.10.0-beta.1")
    parser.add_argument("--destination", type=Path, help="normal candidate directory (must be empty)")
    args = parser.parse_args(argv)
    try:
        build = read_metadata(args.config)
        if build.channel != "beta":
            raise ValueError("private beta builder requires channel beta")
        if build.installation == "normal":
            if not args.version or args.destination is None:
                raise ValueError("normal beta requires --version and --destination")
            from release import prepare_candidate
            prepare_candidate(args.version, args.destination, config=args.config,
                              ffmpeg=args.ffmpeg, ghosts=args.ghosts, ghost_author=args.ghost_author, private=True,
                              check_only=args.check)
            return 0
        if not args.check and needs_reexec(os.environ):
            env = {**os.environ, "PYTHONHASHSEED": "1", "SOURCE_DATE_EPOCH": _source_date_epoch()}
            command = [sys.executable, str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])]
            result = subprocess.run(command, env=env, cwd=REPO, capture_output=True, text=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            return result.returncode
        ffmpeg = check_prerequisites(args.ffmpeg)
        author = candidate_author(args.ghost_author, args.ghosts, build, private=True)
        ghosts = review_collection(args.ghosts) if args.ghosts is not None else None
        if args.check:
            print("Private beta prerequisites verified; no build was run.")
            return 0
        WORK.mkdir(parents=True, exist_ok=True)
        metadata = WORK / CONFIG_FILENAME
        metadata.write_text(json.dumps(build.metadata(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
        import PyInstaller.__main__ as pyi
        pyi.run(beta_args(ffmpeg, metadata))
        archive = package_beta(DIST / BETA_APP_DIR, build, DIST, ghosts=ghosts, ghost_author=author)
        pyi.run(installer_args())
        executable = package_installer(WORK / "installer" / "SM64TrainerBetaSetup.exe", archive, build, DIST)
        print(f"Private beta installer: {executable}\nSHA-256: {executable}.sha256\nPortable ZIP: {archive}")
        return 0
    except (OSError, RuntimeError, ValueError) as failure:
        # Config errors deliberately do not echo submitted values.
        message = "cannot read or write a required build file" if isinstance(failure, OSError) else str(failure)
        print(f"Private beta build refused: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

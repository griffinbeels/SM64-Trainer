# tools/build_exe.py
"""One-command build: `uv run python tools/build_exe.py` ->
dist/SM64Trainer/ (onedir app) + dist/SM64TrainerSetup.exe (bootstrap).

The app is ONEDIR (spec 2026-07-23-incremental-updates): the updater
replaces individual files under the install root, so the build must produce
a folder, not a fused exe. The bootstrap is a tiny stdlib-only ONEFILE
installer, uploaded to releases under the load-bearing asset name
SM64Trainer.exe (old shipped updaters can only install that name).

Reproducibility: PYTHONHASHSEED randomizes compiled bytecode, which would
make every .pyc/PYZ hash differently per build and bloat update deltas.
main() re-execs itself with PYTHONHASHSEED=1 + SOURCE_DATE_EPOCH=<HEAD
commit time> so unchanged sources produce identical bytes across releases.

ffmpeg is bundled automatically: --ffmpeg PATH wins, else the ffmpeg on
PATH. Releases MUST bundle ffmpeg."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEP = ";" if os.name == "nt" else ":"
# Native/binary deps whose data files or submodules PyInstaller's auto
# analysis can miss — collect everything for each.
# proctap in particular: its backend is chosen by name at runtime and its
# WASAPI process-loopback code is a .pyd inside the package, so static
# analysis finds neither — without collecting it the shipped exe silently
# falls back to device-wide loopback (i.e. records the whole desktop).
COLLECT = ["av", "proctap", "pyaudiowpatch", "pycaw",
           "comtypes", "pymem", "webview", "pystray", "numpy", "yt_dlp"]


def needs_reexec(environ) -> bool:
    return environ.get("PYTHONHASHSEED") != "1"


def _source_date_epoch() -> str:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=REPO,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip() or "315532800"
    except Exception:
        return "315532800"    # 1980-01-01, matches the zip timestamp floor


def app_args(ffmpeg: "str | None") -> list[str]:
    argv = [
        str(REPO / "gui_entry.py"),
        "--name", "SM64Trainer",
        "--windowed", "--clean", "--noconfirm",     # onedir: no --onefile
        "--paths", str(REPO / "src"),
        "--icon", str(REPO / "assets" / "ukiki.ico"),
        "--runtime-hook", str(REPO / "tools" / "rthook_comtypes.py"),
        # The UI is READ FROM DISK at runtime (server/app.py _UI_INDEX), not
        # imported, so it must be collected preserving the package path.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'ui'}{SEP}sm64_events/ui",
        # The desktop tray + pywebview window load assets/ukiki.ico at RUNTIME
        # via _asset_path (-> sys._MEIPASS/ukiki.ico when frozen). --icon only
        # embeds it in the PE header (Explorer/taskbar); without bundling it as
        # data the frozen tray fell back to a placeholder. Land it at root.
        "--add-data", f"{REPO / 'assets' / 'ukiki.ico'}{SEP}.",
        # The rank standards seed (bundled_rank_standards() in core/paths.py)
        # is read from sys._MEIPASS when frozen; without this entry the whole
        # ranks feature has no data in the released exe.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'rank_standards.seed.json'}{SEP}.",
        # The default routes/segments seed (bundled_defaults_seed() in
        # core/paths.py) is read from sys._MEIPASS when frozen; without this
        # entry a released exe never seeds the default segments/routes
        # (reconcile_defaults safely no-ops on a missing seed).
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'defaults.seed.json'}{SEP}.",
        # Ladders derived from the Ultimate Sheet (bundled_sheet_ladders() in
        # core/paths.py). Without this entry the 75 sheet-derived strategies
        # work perfectly from source and vanish from the released exe, which is
        # a silent failure: the helper simply returns None and the store falls
        # back to vetted ladders alone.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'sheet_ladders.seed.json'}{SEP}.",
        # The library snapshot itself (bundled_sheet_library()). Same silent
        # failure as above if omitted: the Library page simply has no data.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'sheet_library.seed.json.gz'}{SEP}.",
        # The human's audit corrections to our reading of the sheet
        # (bundled_library_overrides()). Without this entry a refresh run from
        # the released exe rebuilds the library with none of them applied, and
        # the un-corrected (but newer-revisioned) copy then wins over the
        # bundled, corrected one until the next release.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'library_overrides.json'}{SEP}.",
        # Video-liveness verdicts (bundled_video_checks()). Same silent
        # failure as the snapshot if omitted: the released exe filters no dead
        # example links while dev does.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'video_checks.seed.json.gz'}{SEP}.",
    ]
    for pkg in COLLECT:
        argv += ["--collect-all", pkg]
    if ffmpeg:
        argv += ["--add-binary", f"{ffmpeg}{SEP}."]
    return argv


def bootstrap_args() -> list[str]:
    return [
        str(REPO / "bootstrap_entry.py"),
        "--name", "SM64TrainerSetup",
        "--onefile", "--windowed", "--clean", "--noconfirm",
        "--paths", str(REPO / "src"),
        "--icon", str(REPO / "assets" / "ukiki.ico"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["app", "bootstrap", "all"],
                    default="all")
    ap.add_argument("--ffmpeg",
                    help="ffmpeg.exe to bundle (default: the one on PATH)")
    args = ap.parse_args()

    if needs_reexec(os.environ):
        env = {**os.environ, "PYTHONHASHSEED": "1",
               "SOURCE_DATE_EPOCH": _source_date_epoch()}
        return subprocess.run([sys.executable, __file__, *sys.argv[1:]],
                              env=env).returncode

    import PyInstaller.__main__ as pyi

    if args.mode in ("app", "all"):
        ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
        if ffmpeg and not Path(ffmpeg).exists():
            print(f"ffmpeg not found: {ffmpeg}", file=sys.stderr)
            return 2
        if not ffmpeg:
            print("WARNING: no ffmpeg found on PATH and --ffmpeg not given — "
                  "building WITHOUT it; replay will use the in-process "
                  "encoder. Install ffmpeg for a proper release.")
        else:
            print(f"bundling ffmpeg: {ffmpeg}")
        pyi.run(app_args(ffmpeg))
        print("\nBuilt:", REPO / "dist" / "SM64Trainer")
    if args.mode in ("bootstrap", "all"):
        pyi.run(bootstrap_args())
        print("\nBuilt:", REPO / "dist" / "SM64TrainerSetup.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())

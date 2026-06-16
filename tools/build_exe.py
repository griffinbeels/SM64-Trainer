# tools/build_exe.py
"""One-command build: `uv run python tools/build_exe.py` -> dist/SM64Trainer.exe

Bundles Python + all native deps + the UI folder + the Ukiki icon into a
single onefile exe. ffmpeg is bundled automatically: --ffmpeg PATH wins,
else the ffmpeg on PATH is used (so end users never install it themselves).
Only if neither is found does the exe fall back to the in-process PyAV
encoder. Releases MUST bundle ffmpeg — keep an ffmpeg on PATH when building."""
import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEP = ";" if os.name == "nt" else ":"
# Native/binary deps whose data files or submodules PyInstaller's auto
# analysis can miss — collect everything for each.
COLLECT = ["av", "windows_capture", "pyaudiowpatch", "pycaw", "comtypes",
           "pymem", "webview", "pystray", "numpy"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg",
                    help="ffmpeg.exe to bundle (default: the one on PATH)")
    args = ap.parse_args()

    import PyInstaller.__main__ as pyi

    argv = [
        str(REPO / "gui_entry.py"),
        "--name", "SM64Trainer",
        "--onefile", "--windowed", "--clean", "--noconfirm",
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
    ]
    for pkg in COLLECT:
        argv += ["--collect-all", pkg]
    # ffmpeg: explicit flag wins, else auto-discover on PATH so the plain
    # one-command build still bundles it (the released exe must be
    # self-contained — end users never install ffmpeg).
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if ffmpeg:
        ff = Path(ffmpeg)
        if not ff.exists():
            print(f"ffmpeg not found: {ff}", file=sys.stderr)
            return 2
        argv += ["--add-binary", f"{ff}{SEP}."]
        print(f"bundling ffmpeg: {ff}")
    else:
        print("WARNING: no ffmpeg found on PATH and --ffmpeg not given — "
              "building WITHOUT it; replay will use the in-process encoder. "
              "Install ffmpeg (or pass --ffmpeg PATH) for a proper release.")

    pyi.run(argv)
    print("\nBuilt:", REPO / "dist" / "SM64Trainer.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())

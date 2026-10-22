"""Assemble an explicitly private M3 test kit; never build, install or launch.

Portable mode copies a normal verified onedir app and an exact transport
allowlist. Source mode writes a pointer launcher for this machine only.
The approved untrusted client configuration is never printed or embedded in
the launcher. This tool does not prepare a public release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import zipfile

try:
    from tools.bundle_policy import check_bundle_path, ui_inputs
    from tools.build_eos_transport import DLL_SHA256
    from tools.m3_test_instructions import portable_documents
except ModuleNotFoundError:  # Direct `python tools/prepare_m3_test.py`.
    from bundle_policy import check_bundle_path, ui_inputs
    from build_eos_transport import DLL_SHA256
    from m3_test_instructions import portable_documents

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = ("eos-transport.exe", "EOSSDK-Win64-Shipping.dll",
                 "transport-LICENSE.txt", "EOS-ThirdPartySoftwareNotice.txt")
CRT_FILES = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
CONFIG_KEYS = {"product_id", "sandbox_id", "deployment_id", "client_id", "client_secret"}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def member_name(value: str) -> str:
    """A ZIP member must retain exactly the intended Windows-relative name."""
    path = PurePosixPath(value)
    devices = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if (not value or not path.parts or path.is_absolute() or path.as_posix() != value or "\\" in value
            or any(part in {".", ".."} or ":" in part or part.endswith((".", " "))
                   or any(ord(char) < 32 or char in '<>"|?*' for char in part)
                   or part.split(".", 1)[0].lower() in devices for part in path.parts)):
        raise ValueError("Unsafe private kit path.")
    return value


def safe_path(workspace: Path, value: Path, *, directory=False, missing=False) -> Path:
    """Check lexical containment and every existing component before resolving."""
    if ".." in value.parts:
        raise ValueError("Unsafe private kit path.")
    path = value if value.is_absolute() else workspace / value
    path = path.absolute()
    try:
        parts = path.relative_to(workspace).parts
    except ValueError:
        raise ValueError("Private kit paths must stay in the workspace.") from None
    current = workspace
    for part in ("", *parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise ValueError("A required private kit input is missing.") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Private kit paths cannot use links or reparse points.")
    if not missing and not (path.is_dir() if directory else path.is_file()):
        raise ValueError("A required private kit input is missing.")
    return path


def files_in(workspace: Path, directory: Path):
    """Only the explicit app/output tree is walked; private inputs are allowlisted."""
    for base, dirs, files in os.walk(directory, followlinks=False):
        dirs.sort()
        for name in dirs:
            safe_path(workspace, Path(base) / name, directory=True)
            member_name((Path(base) / name).relative_to(directory).as_posix())
        for name in sorted(files):
            path = safe_path(workspace, Path(base) / name)
            relative = member_name(path.relative_to(directory).as_posix())
            yield relative, path


def configuration_bytes(path: Path):
    if not 0 < path.stat().st_size <= 4096:
        raise ValueError("The dedicated player configuration is invalid.")
    raw = path.read_bytes()
    try:
        rows = raw.decode("utf-8-sig").split("\n")
        if rows[-1] == "":
            rows.pop()
        values = {}
        for row in rows:
            if row.endswith("\r"):
                row = row[:-1]
            key, separator, value = row.partition("=")
            if (not separator or key in values or not row or len(row) > 320
                    or any(not 33 <= ord(char) <= 126 for char in row)
                    or not value or len(value) > (256 if key == "client_secret" else 64)
                    or key != "client_secret" and not re.fullmatch(r"[A-Za-z0-9_-]+", value)):
                raise ValueError
            values[key] = value
        if values.keys() != CONFIG_KEYS:
            raise ValueError
    except (UnicodeError, ValueError):
        raise ValueError("The dedicated player configuration is invalid.") from None
    return raw


def runtime_files(workspace: Path, runtime: Path):
    selected = {name: safe_path(workspace, runtime / name) for name in RUNTIME_FILES}
    receipt_path = safe_path(workspace, runtime / "build-receipt.json")
    if receipt_path.stat().st_size > 1 << 20:
        raise ValueError("The transport verification receipt is invalid.")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["sdk"] != "1.19.2.1" or receipt["architecture"] != "x64" or receipt["core_checks_passed"] is not True:
            raise ValueError
        for name in RUNTIME_FILES[:2]:
            expected = receipt["outputs"][name]
            if (type(expected["bytes"]) is not int or expected["sha256"] != digest(selected[name])
                    or expected["bytes"] != selected[name].stat().st_size):
                raise ValueError
        if digest(selected[RUNTIME_FILES[1]]) != DLL_SHA256:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("The transport verification receipt is invalid.") from None
    if any(not file.stat().st_size for file in selected.values()):
        raise ValueError("A required transport input is empty.")
    return selected


def launcher(*, app_name=None, source=None, runtime=None, config=None):
    lines = ["@echo off", "setlocal DisableDelayedExpansion", "chcp 65001 >nul",
             "echo Close every SM64 Trainer and Project64 window before continuing.",
             "echo Install this candidate's renderer from Setup only while Project64 is closed.",
             'choice /C NY /N /M "Are both closed? [N/Y] "', "if not errorlevel 2 exit /b 1"]
    if source is not None:
        for path in (source, runtime, config):
            if any(char in str(path) for char in '%!"^&|<>\r\n'):
                raise ValueError("The source launcher path is unsupported.")
        lines += [f'set "SM64_EOS_HELPER={runtime / "eos-transport.exe"}"',
                  f'set "SM64_EOS_CONFIG={config}"', f'set "SM64_EOS_CACHE={runtime / "cache"}"',
                  f'set "PYTHONPATH={source / "src"}"',
                  f'cd /d "{source}"', f'start "" "{source / ".venv/Scripts/pythonw.exe"}" "{source / "gui_entry.py"}"']
    else:
        lines += [r'set "SM64_EOS_HELPER=%~dp0live-transport\eos-transport.exe"',
                  r'set "SM64_EOS_CONFIG=%~dp0private\m3-player.cfg"',
                  r'set "SM64_EOS_CACHE=%~dp0live-transport\cache"', 'cd /d "%~dp0app"',
                  fr'start "" "%~dp0app\{app_name}"']
    return "\r\n".join([*lines, "exit /b 0", ""])


def _output_paths(workspace, out, archive):
    out = safe_path(workspace, out, directory=True, missing=True)
    relative_output = out.relative_to(workspace).parts
    if not relative_output or relative_output[0].lower() not in {"data", "dist", ".verification", ".tasks"}:
        raise ValueError("The private kit output must use a project-ignored directory.")
    if out == workspace or out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError("The private kit output must be new or empty.")
    zip_path = safe_path(workspace, out.with_name(out.name + ".zip"), missing=True) if archive else None
    if zip_path is not None and zip_path.exists():
        raise ValueError("The private kit archive already exists.")
    return out, zip_path


def _portable_inputs(workspace, app, runtime, out, selected):
    app = safe_path(workspace, app, directory=True)
    if out.is_relative_to(app) or app.is_relative_to(out) or out.is_relative_to(runtime) or runtime.is_relative_to(out):
        raise ValueError("Private kit inputs and output must not overlap.")
    names = [name for name in ("SM64Trainer.exe", "SM64TrainerBeta.exe") if (app / name).is_file()]
    if len(names) != 1:
        raise ValueError("A normal Trainer onedir app is required.")
    # The source selector validates the actual UI tree against Git-selected
    # inputs before those names can approve anything inside the built app.
    ui = ROOT / "src/sm64_events/ui"
    ui_paths = {path.relative_to(ui).as_posix() for path in ui_inputs(ROOT)}
    copied = {}
    for relative, path in files_in(workspace, app):
        check_bundle_path(relative, ui_paths=ui_paths, digest=digest(path))
        copied["app/" + relative] = path
    for name in RUNTIME_FILES:
        copied["live-transport/" + name] = selected[name]
    for name in CRT_FILES:
        copied["live-transport/" + name] = safe_path(workspace, app / "_internal" / name)
        if not copied["live-transport/" + name].stat().st_size:
            raise ValueError("The app VC runtime closure is incomplete.")
    return copied, launcher(app_name=names[0])


def _source_launcher(workspace, source_checkout, runtime, config):
    source_checkout = safe_path(workspace, source_checkout, directory=True)
    safe_path(workspace, source_checkout / "gui_entry.py")
    safe_path(workspace, source_checkout / ".venv/Scripts/pythonw.exe")
    return launcher(source=source_checkout, runtime=runtime, config=config)


def _write_kit(workspace, out, copied, config_body, script, selected, *, portable):
    out.mkdir(parents=True, exist_ok=True)
    for relative, path in copied.items():
        target = out / member_name(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    if portable:
        (out / "private").mkdir()
        (out / "private/m3-player.cfg").write_bytes(config_body)
    (out / "Run-M3-Test.bat").write_bytes(script.encode("utf-8"))
    readme = ("PRIVATE M3 TEST CANDIDATE\n\nClose every Trainer and Project64 window first.\n"
        "Run Run-M3-Test.bat. Install the candidate renderer from Setup only while Project64 is closed.\n"
        "Your normal profile is not copied into this kit. No account or DeviceID cache is included.\n"
        "This is a private test kit, not a release. Do not upload it to the public repository or release feed.\n")
    if not portable:
        readme += "This launcher works only on this machine, using the existing checkout and installed VC runtime.\nIt does not copy the checkout or its private configuration.\n"
    else:
        readme += ("Read START-HERE.txt for first-time Project64/Usamune setup and joining a room.\n"
                   "The EOS DLL uses the three included VC runtime DLLs and the Windows 10+ Universal CRT.\n")
        for name, body in portable_documents().items():
            (out / member_name(name)).write_bytes(body.encode("utf-8"))
    (out / "README-M3-TEST.txt").write_text(readme, encoding="utf-8")
    entries = {name: {"bytes": path.stat().st_size, "sha256": digest(path)} for name, path in files_in(workspace, out)}
    manifest = {"schema": 1, "kind": "private-m3-test", "mode": "portable" if portable else "source",
                "files": entries, "configuration": {"values_in_manifest": False,
                    "location": "private/m3-player.cfg" if portable else "external-private-path"}}
    if not portable:
        manifest["external_inputs"] = {name: {"bytes": path.stat().st_size, "sha256": digest(path)}
                                       for name, path in selected.items()}
        manifest["external_inputs"]["dedicated-player-configuration"] = {
            "bytes": len(config_body), "sha256": hashlib.sha256(config_body).hexdigest()}
    (out / "m3-private-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _archive_kit(workspace, out, zip_path):
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as zipped:
        for relative, path in files_in(workspace, out):
            info = zipfile.ZipInfo(member_name(relative), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            with path.open("rb") as source, zipped.open(info, "w") as target:
                shutil.copyfileobj(source, target, 1 << 20)


def assemble(*, workspace: Path, runtime: Path, config: Path, out: Path,
             app: Path | None = None, source_checkout: Path | None = None, archive=False):
    workspace = workspace.absolute()
    if ".." in workspace.parts or not workspace.is_dir() or workspace.resolve() != workspace:
        raise ValueError("The workspace must be an existing direct directory.")
    if (app is None) == (source_checkout is None) or source_checkout is not None and archive:
        raise ValueError("Select a portable app or a local source launcher.")
    runtime = safe_path(workspace, runtime, directory=True)
    config = safe_path(workspace, config)
    if config != workspace / "data/integrations/eos/m3-proof.cfg":
        raise ValueError("Use only the approved dedicated player configuration.")
    selected = runtime_files(workspace, runtime)
    config_body = configuration_bytes(config)
    out, zip_path = _output_paths(workspace, out, archive)
    if app is not None:
        copied, script = _portable_inputs(workspace, app, runtime, out, selected)
    else:
        copied, script = {}, _source_launcher(workspace, source_checkout, runtime, config)
    manifest = _write_kit(workspace, out, copied, config_body, script, selected, portable=app is not None)
    if zip_path is not None:
        _archive_kit(workspace, out, zip_path)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--app", type=Path)
    mode.add_argument("--source-checkout", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    for name in ("runtime", "config", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--zip", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assemble(workspace=args.workspace, runtime=args.runtime, config=args.config, out=args.out,
                          app=args.app, source_checkout=args.source_checkout, archive=args.zip)
    except (OSError, ValueError):
        print(json.dumps({"ok": False, "error": "private_kit_invalid"}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "mode": report["mode"], "files": len(report["files"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

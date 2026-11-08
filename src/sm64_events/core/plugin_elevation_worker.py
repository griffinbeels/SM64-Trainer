"""Privileged two-phase plugin install. Never imports desktop or user settings.

The helper accepts one authenticated parent connection, installs only the active
bundle's graphics plugin into a verified Project64/Plugin directory under its
fixed name, turns off one fixed setting (frame buffer emulation) in the
renderer's GLideN64.ini there, deletes an older install's leftovers of its own
(fixed names, ours by identity) after the commit, and rolls everything back on
failure. It cannot copy arbitrary files or run commands.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import socket
import sys

if __name__ == "__main__" and not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sm64_events.core.plugin_installation import (  # noqa: E402
    _BUILD, LEFTOVERS, PLUGIN_DLL as PLUGIN, file_operation, in_use, our_leftovers, remove_files, sha256,
    verified_copy,
)
from sm64_events.core.plugin_elevation import receive_packet, send_packet  # noqa: E402
from sm64_events.core.renderer_settings import (  # noqa: E402
    RENDERER_INI as SETTINGS, read as read_settings, turn_off_in_place,
)


def _safe_path(value) -> Path:
    if not isinstance(value, str):
        raise ValueError("Installer path must be text")
    path = Path(value)
    if (not path.is_absolute() or str(path).startswith(("\\\\", "//"))
            or any(part in (".", "..") or part.endswith((".", " "))
                   or ":" in part for part in path.parts[1:])):
        raise ValueError("Installer requires a local absolute path without redirects")
    for component in [*reversed(path.parents), path]:
        try:
            stat = component.lstat()
        except FileNotFoundError:
            continue
        if getattr(stat, "st_file_attributes", 0) & 0x400 or component.is_symlink():
            raise ValueError(f"Installer refuses redirected path: {component}")
    return path


#: the role the graphics plugin's build id carries (tools/build_natives.py)
PLUGIN_ROLE = b"-gpu-runtime"


def validate_request(request):
    from sm64_events.core.capturelayer_win import WinProcesses
    from sm64_events.core.paths import bundled_plugin_dll
    if set(request) != {"pj64_dir", "plugin_dir", "plugin_sha256"}:
        raise ValueError("Invalid administrator installer request")
    pj64, plugin = _safe_path(request["pj64_dir"]), _safe_path(request["plugin_dir"])
    if plugin != pj64 / "Plugin":
        raise ValueError("Administrator install requires Project64's own Plugin folder")
    if WinProcesses.check_folder(str(pj64))["state"] != "ready":
        raise ValueError("Project64.exe could not be verified as a supported version 1.6 build")
    source = bundled_plugin_dll()
    if source is None:
        raise ValueError("This build does not contain the required plugin")
    _safe_path(str(source.absolute()))
    data = source.read_bytes()
    if (data[:2] != b"MZ" or sha256(source) != request["plugin_sha256"]
            or not any(label.endswith(PLUGIN_ROLE + b"\0") for label in _BUILD.findall(data))):
        raise ValueError("Bundled plugin identity changed or has the wrong role")
    return pj64, plugin, source


@contextmanager
def privileged_files(request):
    from sm64_events.core.capturelayer_win import WinProcesses
    from sm64_events.core.plugin_elevation_win import locked_paths
    pj64, plugin, source = validate_request(request)
    parents = {path for item in (pj64, source.parent) for path in (*item.parents, item)}
    directories = sorted(parents, key=lambda path: (len(path.parts), str(path)))
    with locked_paths(directories, [pj64 / "Project64.exe", source], []):
        # Lock handles now prevent source replacement or directory redirection.
        validate_request(request)
        if WinProcesses().pj64_image_path() is not None:
            raise OSError("Close Project64 before installing Practice Replay")
        with file_operation("create plugin folder", plugin):
            plugin.mkdir(exist_ok=True)
        settings = plugin / SETTINGS
        destinations = [plugin / PLUGIN, settings]
        for destination in [*destinations, *(plugin / name for name in LEFTOVERS)]:
            if in_use(destination):
                raise OSError(f"Plugin is in use by another process: {destination}")
        # Pinning creates a missing destination empty, so note which existed first.
        settings_existed = settings.is_file()
        with locked_paths([plugin], [], destinations):
            settings_before = read_settings(settings) if settings_existed else None
            with verified_copy(source, plugin / PLUGIN, reason="elevated_install",
                               rollback=(settings,)) as receipt:
                with file_operation("turn off frame buffer emulation", settings):
                    turn_off_in_place(settings, settings_before)
                try:
                    yield {"installation_receipt": receipt}
                    # The commit means the original user selected the plugin,
                    # so no leftover is Project64's choice any more. A refused
                    # delete puts back what it removed and rolls back the copy.
                    remove_files(our_leftovers(plugin))
                except (ValueError, TypeError) as exc:
                    # restore_on_failure handles OSError. Invalid/tampered
                    # protocol input must roll the same files back too.
                    raise OSError("Administrator transaction was not committed") from exc


class _Rollback(OSError):
    pass


def _error(error, path="Project64 Plugin folder"):
    return {"state": "error", "message": str(error),
            "operation": getattr(error, "operation", "install plugin as administrator"),
            "path": getattr(error, "filename", None) or path,
            "errno": getattr(error, "errno", None), "winerror": getattr(error, "winerror", None),
            "reason": getattr(error, "reason", "io_error")}


def main(argv=None):
    from sm64_events.core.plugin_elevation_win import process_image
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        return 2
    port, secret_text, parent_text = args
    if not port.isdecimal() or not parent_text.isdecimal() or len(secret_text) != 64:
        return 2
    try:
        secret = bytes.fromhex(secret_text)
        parent = process_image(int(parent_text)).resolve()
        own = Path(sys.executable).resolve()
        if getattr(sys, "frozen", False):
            if parent != own:
                return 2
        elif parent.parent != own.parent or parent.name.lower() not in ("python.exe", "pythonw.exe"):
            return 2
        with socket.create_connection(("127.0.0.1", int(port)), timeout=10) as connection:
            connection.settimeout(120)
            send_packet(connection, secret, {"pid": os.getpid()})
            request = receive_packet(connection, secret)
            try:
                with privileged_files(request) as receipts:
                    send_packet(connection, secret, {"state": "prepared", "receipts": receipts})
                    action = receive_packet(connection, secret)
                    if action != {"action": "commit"}:
                        raise _Rollback("Original user did not commit installation")
                send_packet(connection, secret, {"state": "committed"})
            except _Rollback:
                send_packet(connection, secret, {"state": "rolled_back"})
            except (OSError, ValueError) as exc:
                send_packet(connection, secret, _error(exc, request.get("plugin_dir", "Project64")))
        return 0
    except (OSError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

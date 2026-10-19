"""One authenticated, bounded UAC transaction; no privileged user settings."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import struct
import time

from sm64_events.core.plugin_installation import PluginInstallError, sha256

ADMIN_INSTALL_FLAG = "--install-practice-replay-admin"
MAX_PACKET = 128 * 1024
log = logging.getLogger("sm64.capturelayer")


def send_packet(connection, secret: bytes, value: dict):
    payload = json.dumps(value).encode("utf-8")
    if len(payload) > MAX_PACKET:
        raise ValueError("install helper message is too large")
    signature = hmac.digest(secret, payload, hashlib.sha256)
    connection.sendall(struct.pack("!I", len(payload)) + signature + payload)


def receive_packet(connection, secret: bytes) -> dict:
    def exact(length):
        data = bytearray()
        while len(data) < length:
            chunk = connection.recv(length - len(data))
            if not chunk:
                raise OSError("administrator installer disconnected")
            data.extend(chunk)
        return bytes(data)

    size = struct.unpack("!I", exact(4))[0]
    if size > MAX_PACKET:
        raise ValueError("install helper message is too large")
    signature, payload = exact(32), exact(size)
    if not hmac.compare_digest(signature, hmac.digest(secret, payload, hashlib.sha256)):
        raise ValueError("install helper authentication failed")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("invalid install helper message")
    return result


def helper_argv(port: int, secret: bytes) -> list[str]:
    from sm64_events.core.childproc import isolated_python_child
    args = [str(port), secret.hex(), str(os.getpid())]
    # Isolated Python ignores inherited PYTHONPATH/site customization when
    # privileged. The script inserts only its own canonical source directory.
    worker = Path(__file__).with_name("plugin_elevation_worker.py")
    return isolated_python_child(worker, ADMIN_INSTALL_FLAG, args)


def _failure(result):
    original = OSError(result.get("errno"), result.get("message", "Administrator installation failed"))
    original.winerror = result.get("winerror")
    return PluginInstallError(result.get("operation", "install plugin"),
                              result.get("path", "Project64 Plugin folder"), original,
                              reason=result.get("reason", "io_error"))


def _active_sources(source, renderer):
    from sm64_events.core.paths import bundled_plugin_dll, bundled_renderer_dll
    if (bundled_plugin_dll() is None or bundled_renderer_dll() is None
            or source.resolve() != bundled_plugin_dll().resolve()
            or renderer.resolve() != bundled_renderer_dll().resolve()):
        raise PermissionError("Only this Trainer build's bundled plugins can be installed as administrator")


@contextmanager
def elevated_install(pj64_dir: Path, plugin_dir: Path, source: Path, renderer: Path):
    """Keep privileged rollback alive until the original user's commit succeeds.

    Sources are derived by the helper; hashes here bind this attempt to the
    active build. No request file or user-selected privileged source exists.
    """
    from sm64_events.core.plugin_elevation_win import launch_admin
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.25)
        secret = secrets.token_bytes(32)
        with file_failure("validate administrator install", plugin_dir):
            _active_sources(source, renderer)
            request = {"pj64_dir": str(pj64_dir.absolute()), "plugin_dir": str(plugin_dir.absolute()),
                       "wrapper_sha256": sha256(source), "renderer_sha256": sha256(renderer)}
        process = launch_admin(helper_argv(listener.getsockname()[1], secret))
        try:
            deadline = time.monotonic() + 120
            while True:
                try:
                    connection, _ = listener.accept()
                    break
                except TimeoutError as exc:
                    if process.exited() or time.monotonic() >= deadline:
                        raise OSError("Administrator installer did not connect") from exc
            with connection:
                connection.settimeout(120)
                hello = receive_packet(connection, secret)
                if hello != {"pid": process.pid}:
                    raise OSError("Administrator installer identity did not match")
                send_packet(connection, secret, request)
                prepared = receive_packet(connection, secret)
                if prepared.get("state") != "prepared":
                    raise _failure(prepared)
                try:
                    yield prepared["receipts"]
                except BaseException:
                    try:
                        send_packet(connection, secret, {"action": "rollback"})
                        receive_packet(connection, secret)
                    except (OSError, ValueError):
                        # The helper also rolls back on lost parent/timeout.
                        log.warning("Administrator installer rollback acknowledgment failed", exc_info=True)
                    raise
                send_packet(connection, secret, {"action": "commit"})
                result = receive_packet(connection, secret)
                if result.get("state") != "committed":
                    raise _failure(result)
        finally:
            process.close()


@contextmanager
def file_failure(operation, path):
    """Give startup/IPC failures the same useful contract as file operations."""
    try:
        yield
    except PluginInstallError:
        raise
    except (OSError, ValueError) as exc:
        raise PluginInstallError(operation, path, exc) from exc

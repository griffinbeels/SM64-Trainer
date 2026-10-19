"""Read-only frozen beta receipt; no desktop, account instance or worker starts."""
from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import struct
import ssl
import sys
from typing import Callable

from sm64_events.core.build_channel import (
    BETA_APP_DIR, CONFIG_FILENAME, STABLE_APP_DIR, BuildChannel, read_metadata,
)
from sm64_events.core.reporting_setup import CONFIG_FILE as REPORTING_CONFIG, read_config_dsn

FLAG = "--beta-self-check"
_UI = "sm64_events/ui/"
_ASSETS = (
    CONFIG_FILENAME, REPORTING_CONFIG, "ukiki.ico", "rank_standards.seed.json", "defaults.seed.json",
    "sheet_ladders.seed.json", "sheet_library.seed.json.gz", "library_overrides.json",
    "video_checks.seed.json.gz", "behaviours_us.tsv", "behaviours_jp.tsv",
    "symbols_us.tsv", "symbols_jp.tsv",
    # Read relative to their modules, not through core/paths.py, so they live
    # under the package path (round 38: beta.1 died without the policy).
    "sm64_events/data/ranking_policy.json", "sm64_events/data/pad_glyphs_us.npz",
    "sm64_events/data/pad_icons_us.npz", "sm64_events/data/oracle_glyphs_us.npz",
    *(_UI + name for name in (
        "index.html", "app.js", "api.js", "community.js", "components/header.js",
        "components/communityaccount.js", "components/communityprofile.js",
        "components/communitysettings.js", "components/importflow.js",
        "assets/community/discord-clyde-white.svg", "assets/community/google-g.png",
        "assets/community/twitch-glitch-white.svg",
        "assets/community/google-sans.ttf", "assets/community/google-sans-OFL.txt",
    )),
)
_NATIVES = (
    ("sm64_trainer_gfx.dll", 0x14C), ("GLideN64_SM64Trainer.dll", 0x14C),
    ("SM64GpuEncoderV1.dll", 0x8664),
)
_IMPORTS = (
    ("sm64_events.ghost.bundled_beta", ("BundledPopulation", "with_bundled_beta")),
    ("sm64_events.desktop.beta_runtime", ("run_if_needed",)),
    ("sm64_events.community.account", ("CommunityAccount",)),
    ("sm64_events.community.sync", ("CommunitySync",)),
    ("sm64_events.community.codec", ("encode", "decode")),
    ("sm64_events.library.background", ("worker_main",)),
    ("sm64_events.replay.gpuprocess.process_worker", ("main",)),
    ("sm64_events.ghost.sound_player", ("main",)),
    ("sm64_events.core.plugin_elevation_worker", ("main",)),
    ("sentry_sdk", ("init", "capture_exception", "capture_message", "get_client", "new_scope")),
    ("sm64_events.core.error_reporting", ("configure", "capture_exception", "capture_problem", "status", "shutdown")),
    ("sm64_events.core.error_reporting_transport", ("Delivery", "PersistentTransport")),
)


@dataclass(frozen=True)
class ProbeRuntime:
    """Explicit unit-fixture seam; the CLI always obtains packaged authority."""

    build: BuildChannel
    bundle: Path
    executable: Path
    local_appdata: Path
    paths: object
    update_status: Callable[[], dict]


def _local_appdata():
    value = os.environ.get("LOCALAPPDATA")
    return Path(value) if value else Path.home() / "AppData" / "Local"


def _production_runtime():
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        raise ValueError("requires frozen Windows beta")
    from sm64_events.core.build_channel import current_build
    build = current_build()
    if build.channel != "beta":
        raise ValueError("requires beta identity")
    from sm64_events.core import paths

    def update_status():
        # Read actual build policy without a feed check or profile mutation.
        # A synthetic invalid baseline plus frozen=False keeps construction
        # inert; beta_preferences reads the packaged update identity only.
        from sm64_events.core.updater import UpdateService
        def no_http(*args, **kwargs):
            raise RuntimeError("network is forbidden in the beta self-check")
        service = UpdateService(current_version="self-check", frozen=False, http=no_http)
        return {**service.beta_preferences(), "state": "idle" if build.updates_enabled else "disabled"}

    return ProbeRuntime(build, Path(sys._MEIPASS), Path(sys.executable),
                        _local_appdata(), paths, update_status)


def _pe(path, machine, *, dll):
    """Inspect bounded PE headers without loading or invoking a native binary."""
    with path.open("rb") as binary:
        dos = binary.read(64)
        if len(dos) != 64 or dos[:2] != b"MZ":
            return False
        offset = int.from_bytes(dos[60:64], "little")
        if not 64 <= offset <= 1 << 20:
            return False
        binary.seek(offset)
        header = binary.read(24)
    return (len(header) == 24 and header[:4] == b"PE\0\0"
            and int.from_bytes(header[4:6], "little") == machine
            and bool(int.from_bytes(header[22:24], "little") & 0x2000) == dll)


def _file(bundle, relative):
    path = bundle / relative
    return path.resolve().is_relative_to(bundle.resolve()) and path.is_file() and path.stat().st_size > 0


def _reporting_configuration(bundle):
    # The runtime tolerates missing files for older releases; this candidate
    # gate requires the explicit file, even if its DSN is empty.
    if not _file(bundle, REPORTING_CONFIG):
        return False
    read_config_dsn(bundle / REPORTING_CONFIG)
    return True


def _reporting_tls(bundle, load):
    # Verify the actual bundled CA and TLS dependency without connecting,
    # constructing a reporter, or changing a user's reporting preference.
    ca = Path(load("certifi").where()).resolve()
    return (ca.is_relative_to(bundle.resolve()) and ca.is_file()
            and ssl.create_default_context(cafile=str(ca)).cert_store_stats()["x509_ca"] > 0)


def collect_report(*, fixture: ProbeRuntime | None = None, importer=None):
    """Collect bounded checks. Unit fixtures are never selected by CLI or env."""
    report = {"schema": 1, "kind": "frozen-beta-self-check", "ok": False,
              "scope": "bundle imports, assets and isolated runtime settings",
              "not_exercised": ["desktop", "server", "recording", "OAuth", "helper execution", "error delivery"],
              "checks": []}

    def check(name, work):
        try:
            okay = bool(work())
        except Exception:  # noqa: BLE001 -- never serialize transport/import exception text
            okay = False
        report["checks"].append({"name": name, "ok": okay})
        return okay

    try:
        runtime = fixture if fixture is not None else _production_runtime()
    except Exception:  # noqa: BLE001 -- malformed metadata must not echo submitted fields
        check("frozen_beta_identity", lambda: False)
        return report
    build, bundle, paths = runtime.build, runtime.bundle, runtime.paths
    if not check("frozen_beta_identity", lambda: build.channel == "beta"
                 and runtime.executable.name == build.app_dir_name + ".exe"):
        return report
    if not check("packaged_metadata", lambda: read_metadata(bundle / CONFIG_FILENAME) == build):
        return report
    report.update(channel=build.channel, build_id=build.build_id,
                  community_mode="configured" if build.community_url else "offline",
                  installation=build.installation, profile_directory=build.app_dir_name,
                  port=build.default_port, updates_enabled=build.updates_enabled)
    expected = (runtime.local_appdata / build.app_dir_name).resolve()
    check("isolated_profile" if build.legacy_beta else "normal_profile", lambda: paths.data_root().resolve() == expected
          and paths.practice_root() is None
          and paths.APP_DIR_NAME == build.app_dir_name
          and paths.APP_DISPLAY_NAME == build.display_name)
    check("beta_port" if build.legacy_beta else "normal_port", lambda: paths.server_port() == build.default_port)
    if build.legacy_beta:
        check("public_updates_disabled", lambda: not build.updates_enabled
              and runtime.update_status().get("state") == "disabled")
    else:
        check("public_updates_enabled", lambda: build.updates_enabled
              and runtime.update_status().get("updates_enabled") is True)
    check("shared_install_metadata", lambda: paths.capture_layer_settings_path().resolve()
          == (runtime.local_appdata / STABLE_APP_DIR / "data/capture_layer.json").resolve())
    for relative in _ASSETS:
        check("asset:" + relative, lambda relative=relative: _file(bundle, relative))
    check("reporting_configuration", lambda: _reporting_configuration(bundle))
    resolvers = ("bundled_plugin_dll", "bundled_renderer_dll", "bundled_encoder_dll")
    for (name, machine), resolver in zip(_NATIVES, resolvers, strict=True):
        path = bundle / "sm64_events/data/plugin" / name
        def native(path=path, machine=machine, resolver=resolver):
            resolved = getattr(paths, resolver)()
            return (resolved is not None and Path(resolved).resolve() == path.resolve()
                    and _file(bundle, path.relative_to(bundle)) and _pe(path, machine, dll=True))
        check("native:" + name, native)
    ffmpeg = bundle / "ffmpeg.exe"
    check("bundled_ffmpeg", lambda: paths.bundled_ffmpeg() is not None
          and Path(paths.bundled_ffmpeg()).resolve() == ffmpeg.resolve()
          and _file(bundle, "ffmpeg.exe") and _pe(ffmpeg, 0x8664, dll=False))
    check("x64_runtime", lambda: struct.calcsize("P") == 8)

    def shipped_ranking_policy():
        # The first thing create_app reads from the data folder; a pure JSON
        # load with no service, profile or network. beta.1 failed exactly here.
        from sm64_events.ranks.policy import RankingPolicy, _POLICY_PATH
        return _POLICY_PATH.is_file() and bool(RankingPolicy().revision)
    check("ranking_policy_loads", shipped_ranking_policy)
    # Import availability only; no service construction or helper invocation.
    load = importer or importlib.import_module
    for name, members in _IMPORTS:
        def imported(name=name, members=members):
            module = load(name)
            return all(callable(getattr(module, member, None)) for member in members)
        check("import:" + name, imported)
    check("reporting_tls", lambda: _reporting_tls(bundle, load))
    report["ok"] = all(item["ok"] for item in report["checks"])
    return report


def _report_path(value):
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".json":
        raise ValueError("requires absolute JSON report path")
    path = path.resolve()
    if not path.parent.is_dir():
        raise ValueError("report parent must already exist")
    # A maintainer receipt must never create or overwrite an app profile.
    for name in (BETA_APP_DIR, STABLE_APP_DIR, "sm64_tracker"):
        if path.is_relative_to((_local_appdata() / name).resolve()):
            raise ValueError("report cannot be written inside an app profile")
    return path


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    try:
        target = _report_path(args[0])
        report = collect_report()
        target.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 -- no arbitrary paths or exception strings in windowed logs
        return 2
    return 0 if report["ok"] else 2

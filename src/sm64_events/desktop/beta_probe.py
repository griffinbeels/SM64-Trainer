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
from sm64_events.core.update_plan import BUNDLED_NATIVES, LAB_NATIVES

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
    # The setup check's baseline: the setup screen and the elevated install helper read it (the Fixups' hash).
    "sm64_events/data/setup_baseline.json",
    *(_UI + name for name in (
        "index.html", "app.js", "api.js", "community.js", "components/header.js",
        "components/communityaccount.js", "components/communityprofile.js",
        "components/communitysettings.js", "components/importflow.js",
        "assets/community/discord-clyde-white.svg", "assets/community/google-g.png",
        "assets/community/twitch-glitch-white.svg",
        "assets/community/google-sans.ttf", "assets/community/google-sans-OFL.txt",
    )),
)
#: Each bundled native (core/update_plan.py::BUNDLED_NATIVES, in that order):
#: its PE machine and the core/paths.py resolver that must find it.
_NATIVES = tuple(zip(BUNDLED_NATIVES, ((0x14C, "bundled_plugin_dll"), (0x8664, "bundled_encoder_dll")),
                     strict=True))
#: Each Ghost Lab native (core/update_plan.py::LAB_NATIVES, in that order): its
#: PE machine and whether it is a DLL. The hosts are x86 programs beside the
#: x86 plugin they load; the CPU hooks and the cxd4 runner are x64 DLLs the
#: server's own children load. core/paths.py::bundled_lab_file must find each.
_LAB_NATIVES = tuple(zip(LAB_NATIVES, ((0x14C, False), (0x14C, False), (0x14C, False), (0x8664, True),
                                       (0x8664, True)), strict=True))
_IMPORTS = (
    ("sm64_events.ghost.bundled_beta", ("BundledPopulation", "with_bundled_beta")),
    ("sm64_events.desktop.beta_runtime", ("run_if_needed",)),
    ("sm64_events.community.account", ("CommunityAccount",)),
    ("sm64_events.community.sync", ("CommunitySync",)),
    ("sm64_events.community.codec", ("encode", "decode")),
    ("sm64_events.library.background", ("worker_main",)),
    ("sm64_events.replay.gpuprocess.process_worker", ("main",)),
    ("sm64_events.ghost.sound_player", ("main",)),
    ("sm64_events.core.plugin_elevation_worker", ("main", "fixups_source")),
    # Setup's PJ64 Fixups download and the baseline it checks against; the elevated helper imports both.
    ("sm64_events.core.fixups", ("Fixups", "tested_sha256")),
    ("sm64_events.core.setup_profile", ("load_baseline", "SetupProfile")),
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


def _bundled_media_reads():
    # Saved-replay shrinking and a clip's first pts read media in-process
    # through the bundled PyAV (replay/mediaprobe.py): the release ships no
    # ffprobe, which is how every installed copy once failed both quietly
    # (2026-10-25). Write a tiny native-shaped clip, then read it the way the
    # shrink proof does: stream facts, decoded picture times, the packet index.
    import tempfile
    import av
    from sm64_events.replay import mediaprobe
    from sm64_events.replay.media import MEDIA_HZ, MEDIA_TIME_BASE
    ticks = (3000, 4500, 9000)   # uneven, and the first after zero
    with tempfile.TemporaryDirectory(prefix="sm64-self-check-") as folder:
        clip = Path(folder) / "media-reads.mp4"
        with av.open(str(clip), "w", format="mp4", container_options={
                "movie_timescale": str(MEDIA_HZ), "video_track_timescale": str(MEDIA_HZ)}) as out:
            stream = out.add_stream("libx264", options={"bf": "0"})
            stream.width = stream.height = 64
            stream.pix_fmt = "yuv420p"
            stream.codec_context.time_base = MEDIA_TIME_BASE
            for pts in ticks:
                picture = av.VideoFrame(64, 64, "yuv420p")
                picture.pts, picture.time_base = pts, MEDIA_TIME_BASE
                out.mux(stream.encode(picture))
            out.mux(stream.encode())
        media = mediaprobe.probe(clip)
        packets = mediaprobe.native_packet_index(clip) or ()
        times = [round(pts / MEDIA_HZ, 6) for pts in ticks]
        return (media is not None and media.video is not None
                and media.video.codec_name == "h264" and not media.has_audio
                and tuple(p.pts for p in packets) == ticks
                and mediaprobe.frame_times_of(clip) == times
                and mediaprobe.video_start_of(clip) == times[0])


def _shipped_setup_baseline():
    """The setup gate and the elevated install helper check the downloaded PJ64 Fixups against this pin."""
    from sm64_events.core.fixups import tested_sha256
    from sm64_events.core.setup_profile import load_baseline
    pin = tested_sha256(load_baseline())
    return isinstance(pin, str) and len(pin) == 64


def _ghost_lab(report, check, fixture):
    """The Ghost Lab's rows: its modules import here, Unicorn stays out of this program, and the simulation
    program beside it passes its own check (scenelab/probe.py); its receipt rides along whole. Unit fixtures
    have no simulation program beside them."""
    if fixture is None:
        from sm64_events.scenelab.probe import trainer_lab_checks
        report["ghost_lab"] = trainer_lab_checks(check)


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
    for name, (machine, resolver) in _NATIVES:
        path = bundle / "sm64_events/data/plugin" / name
        def native(path=path, machine=machine, resolver=resolver):
            resolved = getattr(paths, resolver)()
            return (resolved is not None and Path(resolved).resolve() == path.resolve()
                    and _file(bundle, path.relative_to(bundle)) and _pe(path, machine, dll=True))
        check("native:" + name, native)
    for name, (machine, dll) in _LAB_NATIVES:
        path = bundle / "sm64_events/data/lab" / name
        def lab_native(name=name, path=path, machine=machine, dll=dll):
            resolved = paths.bundled_lab_file(name)
            return (resolved is not None and Path(resolved).resolve() == path.resolve()
                    and _file(bundle, path.relative_to(bundle)) and _pe(path, machine, dll=dll))
        check("lab_native:" + name, lab_native)
    ffmpeg = bundle / "ffmpeg.exe"
    check("bundled_ffmpeg", lambda: paths.bundled_ffmpeg() is not None
          and Path(paths.bundled_ffmpeg()).resolve() == ffmpeg.resolve()
          and _file(bundle, "ffmpeg.exe") and _pe(ffmpeg, 0x8664, dll=False))
    check("bundled_media_reads", _bundled_media_reads)
    check("x64_runtime", lambda: struct.calcsize("P") == 8)

    def shipped_ranking_policy():
        # The first thing create_app reads from the data folder; a pure JSON
        # load with no service, profile or network. beta.1 failed exactly here.
        from sm64_events.ranks.policy import RankingPolicy, _POLICY_PATH
        return _POLICY_PATH.is_file() and bool(RankingPolicy().revision)
    check("ranking_policy_loads", shipped_ranking_policy)
    check("setup_baseline_loads", _shipped_setup_baseline)
    # Import availability only; no service construction or helper invocation.
    load = importer or importlib.import_module
    for name, members in _IMPORTS:
        def imported(name=name, members=members):
            module = load(name)
            return all(callable(getattr(module, member, None)) for member in members)
        check("import:" + name, imported)
    check("reporting_tls", lambda: _reporting_tls(bundle, load))
    _ghost_lab(report, check, fixture)
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

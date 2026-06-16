"""The perf_log analyzer's pure ranking logic (tools/analyze_perf_log.py)."""
import importlib.util
from pathlib import Path

_MiB = 1024 ** 2
_TOOL = Path(__file__).resolve().parent.parent / "tools" / "analyze_perf_log.py"
_spec = importlib.util.spec_from_file_location("analyze_perf_log", _TOOL)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
analyze = _mod.analyze


def _rec(uptime_s, *, rss=300.0, child_rss_mib=300.0, handles=900, objects=50000,
         growers=None):
    return {
        "uptime_s": uptime_s, "rss_mib": rss, "private_mib": rss,
        "objects": objects, "handles": handles, "gdi": 100, "user": 50,
        "threads": 14, "scratch_mib": 10.0,
        "system": {"commit_bytes": 90 * 1024 * _MiB, "load_pct": 40},
        "children": {"rss_bytes": int(child_rss_mib * _MiB)},
        "gauges": {"ring_bytes": 200 * _MiB},
        "top_growers": growers or [],
    }


def test_empty_is_handled():
    out = analyze([])
    assert "no perf records" in out["verdict"]


def test_child_ffmpeg_growth_is_named_dominant():
    # self flat, ffmpeg child climbs 300 MiB -> 5000 MiB over an hour
    records = [_rec(0, child_rss_mib=300), _rec(1800, child_rss_mib=2600),
               _rec(3600, child_rss_mib=5000)]
    out = analyze(records)
    assert "child_rss(ffmpeg)" in out["verdict"]
    assert out["duration_min"] == 60.0
    child = next(d for d in out["dimensions"] if d["name"] == "child_rss(ffmpeg)")
    assert child["significant"] and child["delta"] == 4700.0
    assert child["per_hour"] == 4700.0          # 4700 MiB over exactly 1 hour


def test_child_growth_ignores_pre_capture_zero_baseline():
    # ffmpeg not attached at t=0 (child_rss 0), attaches ~300 MiB, leaks to 5000
    records = [_rec(0, child_rss_mib=0), _rec(600, child_rss_mib=300),
               _rec(3600, child_rss_mib=5000)]
    out = analyze(records)
    child = next(d for d in out["dimensions"] if d["name"] == "child_rss(ffmpeg)")
    assert child["first"] == 300.0          # first POSITIVE, not the 0 baseline
    assert child["delta"] == 4700.0         # excludes the legit 0->300 attach
    assert "child_rss(ffmpeg)" in out["verdict"]


def test_flat_session_reports_no_significant_growth():
    records = [_rec(0), _rec(1800), _rec(3600)]
    out = analyze(records)
    assert "No dimension grew" in out["verdict"]
    assert all(not d["significant"] for d in out["dimensions"])


def test_handle_leak_with_flat_memory_is_caught():
    # the whole-machine-lag signature: RSS flat, kernel handles exploding
    records = [_rec(0, handles=900), _rec(3600, handles=40000)]
    out = analyze(records)
    assert "handles" in out["verdict"]


def test_tick_compute_latency_growth_is_caught():
    # the CPU-degradation signature: memory flat, per-tick compute climbing
    r0, r1 = _rec(0), _rec(3600)
    r0["gauges"]["tick_ms_ema"] = 0.5
    r1["gauges"]["tick_ms_ema"] = 12.0
    out = analyze([r0, r1])
    assert "tick_compute_ms" in out["verdict"]


def test_gpu_vram_growth_is_named():
    # the D3D-capture-leak signature: process RSS flat, OUR VRAM climbing
    r0, r1 = _rec(0), _rec(3600)
    r0["gpu"] = {"local_usage_bytes": 500 * _MiB}
    r1["gpu"] = {"local_usage_bytes": 6000 * _MiB}
    out = analyze([r0, r1])
    assert "gpu_vram(ours)" in out["verdict"]


def test_pj64_growth_is_named_when_it_leaks():
    # the "it's the emulator, not us" signature
    r0, r1 = _rec(0), _rec(3600)
    r0["processes"] = {"Project64.exe": {"rss_bytes": 300 * _MiB}}
    r1["processes"] = {"Project64.exe": {"rss_bytes": 9000 * _MiB}}
    out = analyze([r0, r1])
    assert "pj64_rss" in out["verdict"]


def test_top_type_growers_passed_through_from_last_record():
    growers = [{"type": "numpy.ndarray", "baseline": 10, "current": 9000,
                "delta": 8990}]
    out = analyze([_rec(0), _rec(3600, growers=growers)])
    assert out["top_type_growers"] == growers

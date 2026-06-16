"""Turn data/perf_log.jsonl (core/perfmon.py output) into a root-cause verdict.

The leak only shows after hours, so the human runs a long session, then runs
this ONE command. It ranks every sampled dimension by growth and names the
dominant climber — self-RSS vs the ffmpeg CHILD process vs GDI/USER/kernel
handles vs system commit vs a specific Python type vs a replay subsystem gauge
— so the next fix targets the measured culprit instead of a guess.

    uv run python tools/analyze_perf_log.py                 # data/perf_log.jsonl
    uv run python tools/analyze_perf_log.py path/to/log.jsonl

`analyze()` is pure (unit-tested in tests/test_analyze_perf_log.py); main()
only reads the file and prints."""
import json
import sys
from pathlib import Path

_MiB = 1024 ** 2

# (label, accessor record->float|None, unit, floor). floor = the absolute delta
# below which growth is noise, not a leak (per unit). Ordered most-suspect first
# for tie display only — the verdict ranks by growth, not this order.
_DIMS = [
    ("self_rss", lambda r: r.get("rss_mib"), "MiB", 256),
    ("self_private", lambda r: r.get("private_mib"), "MiB", 256),
    ("child_rss(ffmpeg)",
     lambda r: _nested(r, "children", "rss_bytes", div=_MiB), "MiB", 256),
    ("system_commit",
     lambda r: _nested(r, "system", "commit_bytes", div=_MiB), "MiB", 1024),
    ("py_objects", lambda r: r.get("objects"), "", 50_000),
    ("handles", lambda r: r.get("handles"), "", 2000),
    ("gdi_objects", lambda r: r.get("gdi"), "", 1000),
    ("user_objects", lambda r: r.get("user"), "", 1000),
    ("threads", lambda r: r.get("threads"), "", 20),
    ("scratch", lambda r: r.get("scratch_mib"), "MiB", 512),
    ("ring(gauge)",
     lambda r: _nested(r, "gauges", "ring_bytes", div=_MiB), "MiB", 512),
    ("tick_compute_ms", lambda r: _nested(r, "gauges", "tick_ms_ema"), "ms", 5),
    ("system_load", lambda r: _nested(r, "system", "load_pct"), "%", 15),
]


def _nested(rec: dict, outer: str, key: str, div: float = 1.0):
    d = rec.get(outer) or {}
    v = d.get(key)
    return None if v is None else v / div


def _series(records: list[dict], get) -> list[float]:
    return [v for v in (get(r) for r in records) if v is not None]


def analyze(records: list[dict]) -> dict:
    """Reduce a list of perf records to {duration_min, dimensions[], verdict,
    top_type_growers[]}. Each dimension carries first/last/max/delta/growth_pct/
    per_hour and a `significant` flag (delta past its floor). Pure."""
    if not records:
        return {"duration_min": 0.0, "dimensions": [], "verdict":
                "no perf records - was data/perf_log.jsonl written? "
                "run a session first", "top_type_growers": []}

    up0 = records[0].get("uptime_s", 0.0)
    up1 = records[-1].get("uptime_s", 0.0)
    dur_h = max(up1 - up0, 0.0) / 3600.0
    dur_min = round((up1 - up0) / 60.0, 1)

    dims = []
    for label, get, unit, floor in _DIMS:
        s = _series(records, get)
        if not s:
            continue
        # first POSITIVE value, not s[0]: child_rss/ring/scratch read 0 until
        # capture attaches (ffmpeg starts after boot), so s[0]=0 would both
        # credit the legit 0->baseline jump as growth AND make growth_pct None
        # (div-by-zero) — which would drop a real multi-GB climber from ranking.
        first = next((v for v in s if v > 0), s[0])
        last, mx = s[-1], max(s)
        delta = last - first
        dims.append({
            "name": label, "unit": unit, "first": round(first, 1),
            "last": round(last, 1), "max": round(mx, 1), "delta": round(delta, 1),
            "growth_pct": (round(100 * delta / first, 1) if first else None),
            "per_hour": (round(delta / dur_h, 1) if dur_h > 0 else None),
            "significant": delta >= floor,
        })

    # verdict: among SIGNIFICANT memory/handle dimensions (exclude the % load
    # gauge — it's a corroborator, not a source), the largest absolute climber
    # within its unit family. Compare MiB-vs-count by growth_pct as the tiebreak.
    candidates = [d for d in dims if d["significant"] and d["name"]
                  not in ("system_load",)]
    candidates.sort(key=lambda d: (d["growth_pct"] or 0), reverse=True)
    if candidates:
        top = candidates[0]
        verdict = (f"DOMINANT GROWTH: {top['name']} grew {top['delta']} "
                   f"{top['unit']} ({top['growth_pct']}%, "
                   f"{top['per_hour']} {top['unit']}/hr) over {dur_min} min. "
                   f"Fix the subsystem that owns it.")
    else:
        verdict = (f"No dimension grew past its noise floor over {dur_min} min "
                   f"- either the session was too short to trigger the leak, "
                   f"or resources are now stable.")

    return {"duration_min": dur_min,
            "dimensions": sorted(dims, key=lambda d: (d["growth_pct"] or 0),
                                 reverse=True),
            "verdict": verdict,
            "top_type_growers": records[-1].get("top_growers", [])}


def _fmt(report: dict) -> str:
    lines = [f"perf_log analysis - {report['duration_min']} min of samples", ""]
    lines.append(f"{'dimension':<20}{'first':>12}{'last':>12}{'max':>12}"
                 f"{'delta':>12}{'%':>8}{'/hr':>12}")
    for d in report["dimensions"]:
        flag = " *" if d["significant"] else ""
        lines.append(f"{d['name']:<20}{d['first']:>12}{d['last']:>12}"
                     f"{d['max']:>12}{d['delta']:>12}"
                     f"{(d['growth_pct'] if d['growth_pct'] is not None else '-'):>8}"
                     f"{(d['per_hour'] if d['per_hour'] is not None else '-'):>12}"
                     f"{flag}")
    growers = report["top_type_growers"]
    if growers:
        lines += ["", "top Python-type growers vs startup baseline:"]
        for g in growers[:10]:
            lines.append(f"  {g['type']:<40} +{g['delta']} "
                         f"({g['baseline']} -> {g['current']})")
    lines += ["", report["verdict"]]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("data") / "perf_log.jsonl"
    if not path.exists():
        print(f"not found: {path} — run a session so core/perfmon.py writes it")
        return 1
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a torn final line from a hard kill
    print(_fmt(analyze(records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

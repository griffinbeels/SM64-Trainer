"""Summarize captures and refuse misleading before/after comparisons."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path


def distribution(values: list[float]) -> dict | None:
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return None
    return {"count": len(values), "mean": statistics.fmean(values),
            **{f"p{p}": values[max(0, math.ceil(len(values) * p / 100) - 1)]
               for p in (50, 95, 99)}, "max": values[-1]}


def counter_delta(values: list[float]) -> dict:
    resets = sum(b < a for a, b in zip(values, values[1:], strict=False))
    return {"delta": values[-1] - values[0] if len(values) > 1 and not resets else None,
            "resets": resets, "samples": len(values)}


def graphics_summary(samples: list[dict], issues: list[str]) -> dict | None:
    graphics = [s.get("replay", {}).get("frame_source_health", {}).get("graphics_profile")
                for s in samples if isinstance(s.get("replay", {}).get("frame_source_health"), dict)]
    available = [g for g in graphics if isinstance(g, dict)]
    if not available:
        return None
    identities = {(g.get("plugin_pid"), g.get("generation"), g.get("producer_instance")) for g in available}
    if len(identities) != 1:
        issues.append("Native graphics profile generation changed")
    final = available[-1]
    for name in final.get("metrics", {}):
        for field in ("count", "total_ms"):
            values = [g.get("metrics", {}).get(name, {}).get(field) for g in available]
            numeric = [value for value in values if isinstance(value, (int, float))]
            if counter_delta(numeric)["resets"]:
                issues.append(f"Native graphics stage {name} reset ({field})")
    return final


def profile_summary(meta: dict, samples: list[dict], issues: list[str]) -> tuple[dict, dict]:
    if meta.get("version") != 1:
        issues.append("Unsupported or missing capture schema version")
    if not meta.get("complete"):
        issues.append("Capture did not complete")
    if len(samples) < 2:
        issues.append("At least two samples are required")
    session = meta.get("profile_session")
    profiles = [meta.get("initial_profile", {})] + [s.get("profile", {}) for s in samples] + [meta.get("final_profile", {})]
    if not session or any(p.get("session_id") != session or "error" in p for p in profiles):
        issues.append("Missing profile data or server profile session changed")
    if any("error" in s.get("replay", {}) or "replay" not in s for s in samples):
        issues.append("Replay status samples missing")
    for key, value in meta.get("recorder_config", {}).items():
        if any(s.get("replay", {}).get(key) != value for s in samples):
            issues.append(f"Recorder configuration changed during capture: {key}")
    stages = meta.get("final_profile", {}).get("stages", {})
    check_boundaries(meta.get("final_profile", {}), issues)
    if not stages:
        issues.append("No instrumented stage observations")
    counters = {}
    names = set().union(*(p.get("counters", {}).keys() for p in profiles))
    for name in sorted(names):
        values = [p.get("counters", {}).get(name) for p in profiles]
        numeric, missing = lazy_counter_values(values)
        counters[name] = counter_delta(numeric)
        if missing:
            issues.append(f"Counter {name} missing in some snapshots")
        if counters[name]["resets"]:
            issues.append(f"Counter {name} reset during capture")
    for name in stages:
        counts = [p.get("stages", {}).get(name, {}).get("count", 0) for p in profiles]
        if counter_delta(counts)["resets"]:
            issues.append(f"Stage {name} reset during capture")
    return stages, counters


def check_boundaries(final: dict, issues: list[str]) -> None:
    if final.get("pending_calls") or final.get("counters", {}).get("completed_outside_window", 0):
        issues.append("Stage calls crossed the capture boundary; durations are censored")
    if final.get("counters", {}).get("stage_limit_rejections", 0):
        issues.append("Stage limit rejected observations")


def lazy_counter_values(values: list) -> tuple[list[float], bool]:
    """Absent before first increment is zero; disappearance later is corruption."""
    numeric = []
    seen = False
    missing = False
    for value in values:
        if isinstance(value, (float, int)):
            seen = True
            numeric.append(value)
        elif seen:
            missing = True
        else:
            numeric.append(0)
    return numeric, missing


def system_metrics(meta: dict, samples: list[dict], issues: list[str]) -> dict:
    metrics = {}
    for key in ("cpu_percent", "memory_used_bytes"):
        metrics["system." + key] = distribution([s["system"][key] for s in samples
                                                 if isinstance(s.get("system", {}).get(key), (int, float))])
        if metrics["system." + key] is None or metrics["system." + key]["count"] != len(samples):
            issues.append(f"Missing system {key}")
    metrics["observer_ms"] = distribution([s["observer_ms"] for s in samples if "observer_ms" in s])
    metrics["wake_excess_ms"] = distribution(meta.get("wake_excess_ms", []))
    metrics["sample_gap_s"] = distribution([b["elapsed_s"] - a["elapsed_s"] for a, b in zip(samples, samples[1:], strict=False)])
    return metrics


def process_metrics(samples: list[dict], issues: list[str]) -> dict:
    processes = {}
    for sample in samples:
        for proc in sample.get("system", {}).get("processes", []):
            if "error" in proc:
                issues.append(f"Process {proc['pid']} unavailable: {proc['error']}")
                continue
            identity = f"{proc['name']}:{proc['pid']}:{proc['created']}"
            processes.setdefault(identity, []).append(proc)
    process_summary = {}
    for identity, rows in processes.items():
        process_summary[identity] = {
            "metrics": {key: distribution([row[key] for row in rows])
                        for key in ("cpu_percent", "rss_bytes", "threads")},
            "counters": {key: counter_delta([row[key] for row in rows])
                         for key in ("read_bytes", "write_bytes")}}
    return process_summary


def replay_metrics(samples: list[dict], issues: list[str]) -> dict:
    statuses = [sample.get("replay", {}) for sample in samples]
    counters = {}
    for key in ("grabs_skipped", "delivered", "skipped", "undecodable", "dropped_by_plugin"):
        values = [status.get(key) if key == "grabs_skipped" else
                  (status.get("frame_source_health") or {}).get(key) for status in statuses]
        numeric = [value for value in values if isinstance(value, (int, float))]
        counters[key] = counter_delta(numeric) if numeric else None
        if numeric and len(numeric) != len(values):
            issues.append(f"Replay counter {key} missing in some samples")
        if numeric and counters[key]["resets"]:
            issues.append(f"Replay counter {key} reset")
    gauges = {key: distribution([status[key] for status in statuses
                                 if isinstance(status.get(key), (int, float))])
              for key in ("encode_backlog", "disk_bytes")}
    return {"counters": counters, "gauges": gauges}


def input_metrics(samples: list[dict], issues: list[str]) -> dict:
    statuses = [(sample.get("health", {}).get("inputs") or {}) for sample in samples]
    result = {}
    for key in ("skips", "skipped_frames", "edge_mismatches", "frames", "samples"):
        values = [status.get(key) for status in statuses]
        numeric = [value for value in values if isinstance(value, (int, float))]
        result[key] = counter_delta(numeric) if numeric else None
        if numeric and len(numeric) != len(values):
            issues.append(f"Input counter {key} missing in some samples")
        if numeric and result[key]["resets"]:
            issues.append(f"Input counter {key} reset")
    return result


def summarize(folder: Path) -> dict:
    meta = json.loads((folder / "capture.json").read_text(encoding="utf-8"))
    sample_path = folder / "samples.jsonl"
    samples = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines()] if sample_path.exists() else []
    issues = list(meta.get("errors", []))
    stages, counters = profile_summary(meta, samples, issues)
    metrics = system_metrics(meta, samples, issues)
    processes = process_metrics(samples, issues)
    graphics = graphics_summary(samples, issues)
    replay = replay_metrics(samples, issues)
    inputs = input_metrics(samples, issues)
    final = meta.get("final_profile", {})
    return {"metadata": meta, "valid": not issues, "issues": sorted(set(issues)),
            "metrics": metrics, "stages": stages, "counters": counters,
            "processes": processes, "graphics": graphics, "gpu": None, "replay": replay, "inputs": inputs,
            "histogram_definition": {key: final.get(key) for key in ("quantile_method", "buckets_ms")},
            "coverage": {"native_graphics": graphics is not None,
                         "input_counters": any(value is not None for value in inputs.values()),
                         "wpr_gpu": "GPU" in meta.get("external_traces", {}).get("wpr_profiles", [])},
            "limitations": ["System sampling does not measure display frame time or GPU engine utilization.",
                            "Stage quantiles describe final cumulative histogram bounds; they are not averaged sample percentiles.",
                            "A matched pair is descriptive evidence, not proof of causation or accuracy."]}


def compare(before: dict, after: dict) -> dict:
    issues = [f"{label}: {issue}" for label, report in (("before", before), ("after", after))
              for issue in report["issues"]]
    for field in ("version", "workload", "machine", "sampling", "recorder_config"):
        if before["metadata"].get(field) != after["metadata"].get(field):
            issues.append(f"Mismatched {field}")
    if set(before["stages"]) != set(after["stages"]):
        issues.append("Mismatched stage coverage")
    if set(before["counters"]) != set(after["counters"]):
        issues.append("Mismatched counter coverage")
    if bool(before.get("graphics")) != bool(after.get("graphics")):
        issues.append("Mismatched native graphics coverage")
    if before.get("histogram_definition") != after.get("histogram_definition"):
        issues.append("Mismatched backend histogram definition")
    if before.get("coverage") != after.get("coverage"):
        issues.append("Mismatched profiler coverage")
    for name, stage in before["stages"].items():
        other = after["stages"].get(name, {})
        if any(stage.get(key) != other.get(key) for key in ("quantile_method", "buckets_ms")):
            issues.append(f"Mismatched histogram definition: {name}")
    result = {"comparable": not issues, "issues": issues, "changes": {}}
    if issues:
        return result
    result["changes"] = measurement_changes(before, after)
    return result


def measurement_changes(before: dict, after: dict) -> dict:
    changes = {}
    for group in ("metrics", "stages"):
        for name, old in before[group].items():
            new = after[group].get(name)
            if not isinstance(old, dict) or not isinstance(new, dict):
                continue
            keys = ("mean", "p50", "p95", "p99", "max") if group == "metrics" else ("count", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms")
            for key in keys:
                if isinstance(old.get(key), (int, float)) and isinstance(new.get(key), (int, float)):
                    changes[f"{group}.{name}.{key}"] = {
                        "before": old[key], "after": new[key], "difference": new[key] - old[key]}
    for name, old in (before.get("graphics") or {}).get("metrics", {}).items():
        new = (after.get("graphics") or {}).get("metrics", {}).get(name, {})
        for key in ("count", "mean_ms", "max_ms", "p50_upper_ms", "p95_upper_ms", "p99_upper_ms"):
            if isinstance(old.get(key), (int, float)) and isinstance(new.get(key), (int, float)):
                changes[f"graphics.{name}.{key}"] = {
                    "before": old[key], "after": new[key], "difference": new[key] - old[key]}
    changes.update(replay_changes(before.get("replay", {}), after.get("replay", {})))
    for name, old in before.get("inputs", {}).items():
        new = after.get("inputs", {}).get(name)
        if isinstance(old, dict) and isinstance(new, dict) and old.get("delta") is not None and new.get("delta") is not None:
            changes[f"inputs.{name}.delta"] = {
                "before": old["delta"], "after": new["delta"], "difference": new["delta"] - old["delta"]}
    return changes


def replay_changes(before: dict, after: dict) -> dict:
    changes = {}
    for group, keys in (("counters", ("delta",)), ("gauges", ("p50", "p95", "p99", "max"))):
        for name, old in before.get(group, {}).items():
            new = after.get(group, {}).get(name)
            if not isinstance(old, dict) or not isinstance(new, dict):
                continue
            for key in keys:
                if isinstance(old.get(key), (int, float)) and isinstance(new.get(key), (int, float)):
                    changes[f"replay.{name}.{key}"] = {
                        "before": old[key], "after": new[key], "difference": new[key] - old[key]}
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(args.before)
    if args.after:
        result = compare(result, summarize(args.after))
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    if not result.get("comparable", result.get("valid", False)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

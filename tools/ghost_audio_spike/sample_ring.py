"""Bounded read-only observation of candidate audio-ring addresses. Never a live gate.

Reuses the project's attachment, byte-order and verified existing-layout readers.
New addresses come only from this experiment's offline ROM/map qualification.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import time

from run import ROOT  # Sets the owned checkout's src import path.
from sm64_events.memory import addresses as A
from sm64_events.memory.layout import layout_for, version_from_argv
from sm64_events.memory.pj64 import Pj64Memory
from sm64_events.memory.version_probe import detect_version


def snapshot(mem: Pj64Memory, addresses: dict, layout) -> dict | None:
    started = time.perf_counter()
    clock = mem.read_u32(layout.global_timer)
    producer = mem.read_u8(addresses["sSoundRequestCount"])
    consumer = mem.read_u8(addresses["sNumProcessedSoundRequests"])
    first = mem.read_words(addresses["sSoundRequests"], 256 * 8)
    middle = mem.read_u8(addresses["sSoundRequestCount"])
    second = mem.read_words(addresses["sSoundRequests"], 256 * 8)
    end = mem.read_u8(addresses["sSoundRequestCount"])
    consumed = mem.read_u8(addresses["sNumProcessedSoundRequests"])
    mario = mem.read_u32(layout.mario_object)
    action = mem.read_u32(layout.mario_struct + A.MARIO_ACTION_OFF)
    if producer != middle or middle != end or first != second or clock != mem.read_u32(layout.global_timer):
        return None
    entries = list(struct.iter_unpack("<II", first))
    pose = None
    if A.KSEG0_BASE <= mario < A.KSEG0_BASE + A.RDRAM_FULL_SIZE - 0x60:
        # VERIFY: source structure's gfx.pos and gfx.cameraToObject, not shipped fields.
        pose = {"world_gfx": struct.unpack("<fff", mem.read_words(mario + 0x20, 12)),
                "camera_relative": struct.unpack("<fff", mem.read_words(mario + 0x54, 12))}
    return {"clock": clock, "producer": producer, "consumer_before": consumer,
            "consumer_after": consumed, "entries": entries, "mario": mario,
            "mario_sound_source": mario + 0x54, "action": action, "pose": pose,
            "read_ms": (time.perf_counter() - started) * 1000}


def observe(mem: Pj64Memory, addresses: dict, layout, seconds: float) -> dict:
    start = time.perf_counter()
    previous = None
    initial = None
    latest = None
    observations = []
    stats = Counter()
    actions = Counter()
    intervals, read_times = [], []
    last_sample_at = start
    while time.perf_counter() - start < seconds:
        now = time.perf_counter()
        current = snapshot(mem, addresses, layout)
        stats["polls"] += 1
        if current is None:
            stats["unstable_snapshots"] += 1
        else:
            intervals.append((now - last_sample_at) * 1000)
            last_sample_at = now
            read_times.append(current["read_ms"])
            actions[f"0x{current['action']:08x}"] += 1
            latest = current
            if initial is None:
                initial = current
            if previous is not None:
                delta = (current["producer"] - previous["producer"]) & 255
                clock_delta = current["clock"] - previous["clock"]
                stats["largest_modulo_producer_delta"] = max(stats["largest_modulo_producer_delta"], delta)
                stats["largest_forward_clock_delta"] = max(stats["largest_forward_clock_delta"], clock_delta)
                changed = [i for i in range(256) if current["entries"][i] != previous["entries"][i]]
                if clock_delta < 0 or current["mario"] != previous["mario"]:
                    stats["clock_or_object_discontinuities"] += 1
                elif delta or changed:
                    # Equal modulo producer can hide 256 requests; changed slots
                    # expose some aliases but cannot prove absence of equal rewrites.
                    if not delta and changed:
                        stats["changed_slots_with_equal_producer"] += 1
                    if len(changed) > delta:
                        stats["changed_slots_exceed_modulo_delta"] += 1
                    added = [current["entries"][(previous["producer"] + i) & 255]
                             for i in range(delta)]
                    mario_events = [entry for entry in added if entry[1] == current["mario_sound_source"]]
                    stats["requests_modulo_lower_bound"] += delta
                    stats["mario_requests_modulo_lower_bound"] += len(mario_events)
                    stats["producer_changes_same_game_clock"] += int(delta > 0 and clock_delta == 0)
                    stats["events_already_consumed_at_observation"] += len(added) if current["producer"] == current["consumer_after"] else 0
                    observations.append({key: value for key, value in current.items() if key != "entries"}
                                        | {"seconds": now - start, "delta": delta, "changed_slots": changed,
                                           "new_entries": added, "mario_entries": mario_events})
            previous = current
        time.sleep(1 / 240)
    if initial is None:
        raise RuntimeError("No stable snapshot was observed")
    return {"elapsed_s": time.perf_counter() - start, "statistics": dict(stats),
            "actions": dict(actions), "initial": initial, "final": latest,
            "observations": observations, "max_poll_gap_ms": max(intervals),
            "max_read_ms": max(read_times), "mean_read_ms": sum(read_times) / len(read_times),
            "limits": "Modulo deltas are lower bounds; no human gate or renderer-boundary completeness proof."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--version", default="us")
    options = parser.parse_args()
    version = version_from_argv()
    if version != "us" or not 0 < options.seconds <= 30:
        raise ValueError("This candidate probe supports US and at most 30 seconds")
    qualification = json.loads(options.qualification.read_text())
    if not qualification.get("producer_layout_matches_map"):
        raise ValueError("Offline producer instructions have not qualified the ring layout")
    addresses = {key: int(value, 16) for key, value in qualification["symbols"].items()}
    if any(not A.KSEG0_BASE <= value < A.KSEG0_BASE + A.RDRAM_FULL_SIZE - 2048
           for value in addresses.values()):
        raise ValueError("Candidate address leaves RDRAM")
    mem = Pj64Memory()
    try:
        if not mem.attach() or detect_version(mem) != version:
            raise RuntimeError("No verified Project64 US attachment")
        code = mem.read_block(addresses["play_sound"], qualification["play_sound_bytes"])
        if hashlib.sha256(code).hexdigest() != qualification["play_sound_sha256"]:
            raise RuntimeError("Live producer differs from the offline-qualified ROM")
        result = observe(mem, addresses, layout_for(version), options.seconds)
        result["producer_code_matches_offline_rom"] = True
        result["qualification_sha256"] = hashlib.sha256(options.qualification.read_bytes()).hexdigest()
        result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        result["checkout"] = str(ROOT)
        options.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items()
                          if key not in ("initial", "final", "observations")}, indent=2))
        print("Retained Mario entries:", sum(entry[1] == result["initial"]["mario_sound_source"]
                                              for entry in result["initial"]["entries"]))
    finally:
        mem.detach()


if __name__ == "__main__":
    main()

"""Explicit script entry point for the isolated Python x64 helper candidate."""

from pathlib import Path
import sys
import os
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from sm64_events.replay.gpuprocess.process_protocol import (
    read_frame,
    frame,
    write_all,
    REQUEST,
    REPLY,
)
from sm64_events.replay.gpuencoder_abi import Status, initialize, snapshot

nonce = sys.argv[1]
j = int(sys.argv[2])
p = int(sys.argv[3])
mode = None
owners = {}
acquired = released = count = 0
close_pending = False
while True:
    request, _ = read_frame(sys.stdin.buffer, REQUEST, max_json=j, max_packet=0)
    command = request["command"]
    mode = command.get("fixture", mode)
    op = command["op"]
    if mode == "slow-open" and op == "Open":
        time.sleep(0.3)
    if mode == "hang":
        sys.stderr.buffer.write(b"Z" * 50000)
        sys.stderr.buffer.flush()
        while True:
            time.sleep(10)
    packet = None
    payload = b""
    returns = []
    status = snapshot(initialize(Status))
    status.update(state=1, encoder_state=1, owner_thread=1, adapter_low=84637)
    if op == "Submit":
        slot = command["slot"]
        owners[slot] = {
            "slot": slot,
            "bridge_token": command["bridge_token"],
            "serial": command["serial"],
        }
        acquired += 1
        count += 1
        payload = b"compressed-fixture"
        packet = {n: command[n] for n in ["serial", "pts", "encoder_duration"]}
        packet.update(keyframe=True, bytes=len(payload))
    pending = op == "Close" and mode == "close-pending" and not close_pending
    if pending:
        close_pending = True
    if op == "Poll" or (op == "Close" and mode != "close-missing-return" and not pending):
        returns = list(owners.values())
        released += len(owners)
        owners.clear()
    status.update(
        held_mask=sum(1 << slot for slot in owners),
        pending_mask=sum(1 << slot for slot in owners),
        acquired=acquired,
        released=released,
        submitted=count,
        completed=count,
        delivered=count,
    )
    if op == "Close":
        status.update(state=3 if pending else 4, encoder_state=3 if pending else 4)
        if mode == "close-missing-return":
            status.update(held_mask=0, pending_mask=0, released=acquired)
    if op == "Poll" and mode == "receipt-bool-mask":
        status["held_mask"] = False
    if op == "Poll" and mode == "receipt-short-status":
        status = {"held_mask": 0}
    reply = {
        "nonce": nonce if mode != "wrong-nonce" else "wrong",
        "request_id": request["request_id"],
        "result": 10 if pending else 0,
        "error": None,
        "worker_disposal_required": False,
        "status": status,
        "retained": None,
        "key_returns": returns,
        "packet": packet,
        "identity": {"pid": os.getpid()},
    }
    write_all(sys.stdout.buffer, frame(REPLY, reply, payload, max_json=j, max_packet=p))
    if mode == "duplicate":
        write_all(
            sys.stdout.buffer, frame(REPLY, reply, payload, max_json=j, max_packet=p)
        )
    if op == "Close" and not pending:
        break

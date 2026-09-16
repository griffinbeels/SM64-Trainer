"""Sequential x64 helper. No GPU operation occurs before explicit Open."""

from pathlib import Path
import ctypes as C
import hashlib
import sys
import logging

log = logging.getLogger(__name__)
from ..gpuencoder import EncoderClient, EncoderFailure, Result
from .process_protocol import exact, uint, read_frame, frame, write_all, REQUEST, REPLY


class Worker:
    def __init__(self, max_packet):
        self.client = None
        self.packet = None
        self.max_packet = max_packet
        self.slots = {}
        self.identity = None

    def receive(self, packet):
        if self.packet is not None or len(packet.payload) > self.max_packet:
            return False
        self.packet = packet
        return True

    def observe(self):
        if self.client is None:
            return None, None, []
        state = self.client.status()
        retained = (
            None
            if self.client.closed or self.client._uncertain
            else self.client.retained()
        )
        returned = []
        for slot, identity in list(self.slots.items()):
            if not state["held_mask"] & (1 << slot):
                returned.append(dict(slot=slot, **identity))
                del self.slots[slot]
        return state, retained, returned

    def _dispatch(self, command):
        op = command.get("op")
        if op == "Open":
            exact(command, ["op", "dll_path", "adapter_luid", "names", "options"])
            if self.client is not None:
                raise ValueError("session already opened")
            if command["options"].get("max_packet_bytes", 0) > self.max_packet:
                raise ValueError("packet bound exceeds channel")
            self.client = EncoderClient(
                dll_path=command["dll_path"],
                adapter_luid=command["adapter_luid"],
                names=command["names"],
                options=command["options"],
                on_packet=self.receive,
            )
            path = Path(command["dll_path"]).resolve()
            self.identity = {
                "pid": __import__("os").getpid(),
                "pointer_bits": C.sizeof(C.c_void_p) * 8,
                "executable": sys.executable,
                "dll_path": str(path),
                "dll_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            result = Result.OK
        elif self.client is None:
            raise ValueError("Open required first")
        elif op == "Submit":
            exact(
                command,
                [
                    "op",
                    "slot",
                    "bridge_token",
                    "serial",
                    "pts",
                    "encoder_duration",
                    "force_idr",
                ],
            )
            uint(command["bridge_token"], "bridge token", True)
            slot = command["slot"]
            if type(slot) is not int or slot not in (0, 1):
                raise ValueError("slot outside fixed pool")
            if slot in self.slots:
                result = Result.PENDING
            else:
                result = self.client.submit(
                    slot,
                    **{
                        n: command[n]
                        for n in ["serial", "pts", "encoder_duration", "force_idr"]
                    },
                )
                if result == Result.OK:
                    self.slots[slot] = {
                        "bridge_token": command["bridge_token"],
                        "serial": command["serial"],
                    }
        elif op == "Repeat":
            exact(
                command,
                [
                    "op",
                    "generation",
                    "serial",
                    "pts",
                    "encoder_duration",
                    "force_idr",
                ],
            )
            result = self.client.repeat(
                **{
                    n: command[n]
                    for n in [
                        "generation",
                        "serial",
                        "pts",
                        "encoder_duration",
                        "force_idr",
                    ]
                }
            )
        elif op in ("Poll", "Close"):
            exact(command, ["op"])
            result = self.client.poll() if op == "Poll" else self.client.close()
        else:
            raise ValueError("unknown operation")
        return result

    def command(self, command):
        self.packet = None
        error = None
        disposal = False
        try:
            result = self._dispatch(command)
        except EncoderFailure as exc:
            result = exc.result if exc.result is not None else Result.STATE
            error = str(exc)[:512]
            disposal = exc.worker_disposal_required
        except BaseException as exc:
            log.exception("GPU helper command or status failed")
            result = Result.STATE
            error = type(exc).__name__ + ": " + str(exc)[:480]
        try:
            status, retained, returned = self.observe()
        except BaseException as exc:
            log.exception("GPU helper command or status failed")
            status = retained = None
            returned = []
            error = error or ("status failed: " + type(exc).__name__)
            disposal = True
        payload = b""
        packet = None
        if self.packet is not None and result == Result.OK and not error:
            p = self.packet
            payload = p.payload
            packet = {
                "serial": p.serial,
                "pts": p.pts,
                "encoder_duration": p.encoder_duration,
                "keyframe": p.keyframe,
                "bytes": len(payload),
            }
        return {
            "result": int(result),
            "error": error,
            "worker_disposal_required": disposal,
            "status": status,
            "retained": retained,
            "key_returns": returned,
            "packet": packet,
            "identity": self.identity,
        }, payload


def run(nonce, max_json, max_packet):
    worker = Worker(max_packet)
    expected = 1
    while True:
        request, _payload = read_frame(
            sys.stdin.buffer, REQUEST, max_json=max_json, max_packet=0
        )
        exact(request, ["nonce", "request_id", "command"])
        if (
            request["nonce"] != nonce
            or uint(request["request_id"], "request id", True) != expected
        ):
            raise ValueError("request identity mismatch")
        expected += 1
        reply, data = worker.command(request["command"])
        reply.update(nonce=nonce, request_id=request["request_id"])
        write_all(
            sys.stdout.buffer,
            frame(REPLY, reply, data, max_json=max_json, max_packet=max_packet),
        )
        if (
            reply["worker_disposal_required"]
            or reply["error"]
            or (request["command"]["op"] == "Close" and reply["result"] == 0)
        ):
            return
